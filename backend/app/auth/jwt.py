"""Authentication utilities — JWT tokens and password hashing.

v2.38.6: Replaced passlib with direct bcrypt usage.
passlib 1.7.4 is unmaintained and silently fails password verification
with bcrypt >= 4.0, causing login to always reject valid passwords.

v2.53.4: Wrapped bcrypt calls in asyncio.to_thread() to prevent blocking
the async event loop during password hash/verify operations (~250-500ms each).

v2.63.8 (FIX-2): Added a 60-second TTL in-process cache around the User
lookup in ``get_current_user``. Eliminates the per-request
``SELECT * FROM users`` for back-to-back authenticated calls (the
Settings page alone fan-outs 34 of these). Token revocation latency
becomes <=60s; documented in ADR-0005.

ADR-0047 (operator Cloudflare Access SSO, noc-master ADR-0246 decision 2 /
fleet-sso-conversion-roadmap Phase 4.4): ``get_current_user`` is the single
dependency all 25 operator routers use (verified — the customer-facing
client portal / intake / TOS-acceptance surfaces use the entirely separate
``app.auth.client_auth`` module and never import this one). It is therefore
the one place a CF-Access-verified identity needs to be wired in for every
operator route to pick it up automatically — additive, same shape as the
marketing pilot's Express ``authMiddleware``.
"""

import asyncio
import logging
import re
import secrets
import time
from datetime import datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cf_access import (
    extract_cf_access_token,
    is_cf_access_configured,
    verify_cf_access_assertion,
)
from app.config import settings
from app.database import get_db
from app.models.cf_access_identity import CfAccessIdentity
from app.models.user import User

logger = logging.getLogger("doc.auth")

# auto_error=False: a request authenticating purely via Cloudflare Access
# carries NO `Authorization: Bearer ...` header at all (the identity rides
# the separate `Cf-Access-Jwt-Assertion` header instead). With the default
# auto_error=True, FastAPI's HTTPBearer dependency would 403 such a request
# before get_current_user's body ever ran. We resolve auth explicitly below
# instead.
security = HTTPBearer(auto_error=False)

# ── Password complexity requirements ──────────────────────────────────
PASSWORD_MIN_LENGTH = 10
PASSWORD_RULES = [
    ("At least 10 characters", lambda p: len(p) >= 10),
    ("At least one uppercase letter", lambda p: bool(re.search(r"[A-Z]", p))),
    ("At least one lowercase letter", lambda p: bool(re.search(r"[a-z]", p))),
    ("At least one number", lambda p: bool(re.search(r"\d", p))),
    ("At least one special character (!@#$%^&*...)", lambda p: bool(re.search(r"[^A-Za-z0-9]", p))),
]


def check_password_complexity(password: str) -> list[str]:
    """Return list of unmet complexity requirements. Empty list = compliant."""
    return [desc for desc, check in PASSWORD_RULES if not check(password)]


def is_password_compliant(password: str) -> bool:
    """Check if a password meets all complexity requirements."""
    return len(check_password_complexity(password)) == 0


def _hash_password_sync(password: str) -> str:
    """Synchronous bcrypt hash — call via hash_password() for async safety."""
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    result = hashed.decode("utf-8")
    logger.debug("Password hashed successfully (length=%d, hash_prefix=%s)", len(password), result[:7])
    return result


def _verify_password_sync(plain_password: str, hashed_password: str) -> bool:
    """Synchronous bcrypt verify — call via verify_password() for async safety."""
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        result = bcrypt.checkpw(pw_bytes, hash_bytes)
        logger.debug(
            "Password verify: result=%s, plain_len=%d, hash_prefix=%s",
            result, len(plain_password), hashed_password[:7],
        )
        return result
    except Exception as exc:
        logger.error("Password verification crashed: %s (hash_prefix=%s)", exc, hashed_password[:7] if hashed_password else "EMPTY")
        return False


def hash_password(password: str) -> str:
    """Hash a password using bcrypt directly (no passlib).

    This is the synchronous version — used during startup/seed where
    we're not in an async request context. For async endpoints, use
    hash_password_async() instead.
    """
    return _hash_password_sync(password)


async def hash_password_async(password: str) -> str:
    """Hash a password without blocking the async event loop.

    Runs bcrypt in a thread pool so other requests aren't blocked
    during the ~250-500ms hashing operation.
    """
    return await asyncio.to_thread(_hash_password_sync, password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a bcrypt hash directly (no passlib).

    This is the synchronous version — used during startup/seed where
    we're not in an async request context. For async endpoints, use
    verify_password_async() instead.
    """
    return _verify_password_sync(plain_password, hashed_password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Verify a password without blocking the async event loop.

    Runs bcrypt in a thread pool so other requests aren't blocked
    during the ~250-500ms verification operation.
    """
    return await asyncio.to_thread(_verify_password_sync, plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.jwt_refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# ── User-row cache (FIX-2, v2.63.8) ────────────────────────────────────
# In-process TTL cache for the per-request ``SELECT * FROM users`` that
# every authenticated endpoint runs. Keyed by (username, first 16 chars
# of token) so a token rotation immediately invalidates. Cached payload
# is a dict of safe-to-replay primitives — we instantiate a transient
# ORM `User` on each hit so downstream attribute access works the same
# way as a session-bound instance.
#
# Failure mode: container restart wipes the cache (forces revalidation),
# token revocation latency is bounded at ``_USER_CACHE_TTL`` seconds.
# For self-hosted single-operator deployment this is acceptable — see
# ADR-0005 §FIX-2 for the documented staleness window.
_USER_CACHE_TTL = 60.0  # seconds
_USER_CACHE_MAX = 1000  # entries — defensive ceiling
_user_cache: dict[tuple[str, str], tuple[dict, float]] = {}


def _user_cache_evict_expired(now: float) -> None:
    """Drop expired entries from the user cache (best-effort housekeeping)."""
    expired = [k for k, (_, exp) in _user_cache.items() if exp < now]
    for k in expired:
        _user_cache.pop(k, None)


# ── Cloudflare Access identity resolution (ADR-0047; modeled on the
# marketing pilot's `resolveCfAccessUser`, marketing ADR-0099, corrected
# 2026-09-21 after a security review found that pilot's first draft
# auto-granted 'admin' and could silently adopt an unrelated local account
# by matching `users.username` against the verified email) ────────────────
#
# A CF-Access-authenticated request never carries a local session/JWT — it
# has no `users.id` to point at. Without one, any route reading `user.id`
# would crash or misbehave. The fix is the SAME as marketing's: a dedicated
# mapping table (`cf_access_identities`, email -> users.id), populated ONLY
# here, never by a `WHERE username = <email>` lookup. A DroneOpsCommand
# operator CAN rename their local username to anything (this app places no
# charset restriction on `PUT /api/auth/account`, unlike marketing's) —
# meaning a local row could coincidentally hold the exact string this
# function would otherwise try to use, so resolution here NEVER trusts
# `users.username` as an identity key, only the mapping table's `user_id`
# foreign key. A newly-provisioned shadow row's username is namespaced
# (`cf-access:<email>`) purely for human-readable log lines (see
# app/routers/reports.py's audit log) — it is NEVER looked up by string.
#
# This app has no `role` column on `users` at all (single-operator tool —
# no route reads a role to permit/deny anything), so unlike marketing there
# is no admin-grant question here. The shadow row's password is a random,
# never-returned, never-derivable value; it exists only so downstream
# `user.id` references have a real foreign key, never as a usable local
# login credential.
async def resolve_cf_access_user(db: AsyncSession, email: str) -> User:
    result = await db.execute(
        select(User)
        .join(CfAccessIdentity, CfAccessIdentity.user_id == User.id)
        .where(CfAccessIdentity.email == email)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    shadow_password = await hash_password_async(secrets.token_urlsafe(48))
    shadow_username = f"cf-access:{email}"
    new_user = User(username=shadow_username, hashed_password=shadow_password, is_active=True)
    db.add(new_user)
    try:
        await db.flush()  # assigns new_user.id, surfaces a UNIQUE violation now
        db.add(CfAccessIdentity(email=email, user_id=new_user.id))
        await db.flush()
        await db.commit()
        logger.info("[CF-ACCESS] provisioned shadow user for email=%s", email)
        return new_user
    except IntegrityError:
        # Either `cf_access_identities.email` (a concurrent request
        # provisioned it first) or `users.username` (something else already
        # holds the exact `cf-access:<email>` string) raced us. Roll back
        # our attempt and re-check the mapping table — the only source of
        # truth for CF-Access identity resolution.
        await db.rollback()
        result = await db.execute(
            select(User)
            .join(CfAccessIdentity, CfAccessIdentity.user_id == User.id)
            .where(CfAccessIdentity.email == email)
        )
        winner = result.scalar_one_or_none()
        if winner is not None:
            return winner
        # A genuine `users.username` collision with no matching mapping row
        # — never silently adopt whatever holds that username. Deny loudly.
        logger.error(
            "[CF-ACCESS] shadow-user provisioning failed for email=%s "
            "(username collision on '%s' with no cf_access_identities row)",
            email, shadow_username,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Access identity provisioning failed — contact the operator",
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Cloudflare Access JWT (ADR-0047, additive). Structurally dark until
    # CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD are both set — see
    # app/auth/cf_access.py. A failed/absent Access credential falls
    # through to the local bearer-token path below rather than 401ing
    # immediately: Access not applying to THIS credential does not mean
    # the request is unauthenticated, only that this one credential didn't
    # work, and a session token remains fully valid until Step B
    # (LOCAL_LOGIN_DISABLED) explicitly turns it off.
    if is_cf_access_configured():
        assertion = extract_cf_access_token(request)
        if assertion:
            result = await verify_cf_access_assertion(assertion)
            if result.ok:
                return await resolve_cf_access_user(db, result.email)

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")
        if username is None or token_type != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    cache_key = (username, token[:16])
    now = time.time()
    cached = _user_cache.get(cache_key)
    if cached is not None and cached[1] > now:
        # Hit — rebuild a transient ORM instance from cached primitives.
        return User(**cached[0])

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Cache only safe-to-replay primitives. We deliberately omit any
    # relationship-loaded attributes; downstream code reads the columns
    # below.
    _user_cache[cache_key] = (
        {
            "id": user.id,
            "username": user.username,
            "hashed_password": user.hashed_password,
            "is_active": user.is_active,
            "created_at": user.created_at,
        },
        now + _USER_CACHE_TTL,
    )
    if len(_user_cache) > _USER_CACHE_MAX:
        _user_cache_evict_expired(now)
    return user


def invalidate_user_cache(username: str | None = None) -> None:
    """Drop cached user row(s). Called on logout / password change / disable.

    - ``username=None`` clears everything (used by tests + container shutdown).
    - Otherwise drops every cached entry for that username (covers all
      tokens issued to them).
    """
    if username is None:
        _user_cache.clear()
        logger.info("user_cache_cleared scope=all")
        return
    keys = [k for k in _user_cache if k[0] == username]
    for k in keys:
        _user_cache.pop(k, None)
    logger.info("user_cache_cleared scope=user username=%s entries=%d", username, len(keys))
