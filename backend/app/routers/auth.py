"""Authentication router — login, account management, token refresh, setup wizard.

v2.56.0: Credentials managed entirely via UI. No env vars for passwords.
First visit shows setup wizard when no users exist in database.
To reset: docker compose exec backend python reset_to_setup.py
"""

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from slowapi import Limiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.auth.cf_access import (
    extract_cf_access_token,
    is_cf_access_configured,
    verify_cf_access_assertion,
)
from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    hash_password_async,
    invalidate_user_cache,
    resolve_cf_access_user,
    verify_password,
    verify_password_async,
    check_password_complexity,
    PASSWORD_RULES,
)
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.utils.client_ip import get_trusted_client_ip

logger = logging.getLogger("doc.auth")


class AccountUpdateRequest(BaseModel):
    current_password: str
    new_username: str | None = None
    new_password: str | None = None


# ── Login lockout: 5 failed attempts in 120s → locked for 2 minutes ──
LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW_SECS = 120
LOCKOUT_DURATION_SECS = 120

# {ip_address: [timestamp, timestamp, ...]}
_failed_attempts: dict[str, list[float]] = defaultdict(list)
# {ip_address: lockout_until_timestamp}
_lockouts: dict[str, float] = {}

router = APIRouter(prefix="/api/auth", tags=["auth"])
# v2.91.0 (Phase 7 hardening) — was get_remote_address (nginx's own IP on
# every request, see app/utils/client_ip.py). The login lockout below keys
# on this same function's output, so this was the mechanism by which a
# stranger's failed logins could lock the operator out of their own
# instance: every caller shared one IP-keyed bucket.
limiter = Limiter(key_func=get_trusted_client_ip)


def _check_lockout(ip: str) -> None:
    """Raise 429 if the IP is currently locked out."""
    until = _lockouts.get(ip)
    if until and time.time() < until:
        remaining = int(until - time.time())
        logger.warning("Login attempt from locked-out IP %s (%ds remaining)", ip, remaining)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Account locked for {remaining} seconds.",
        )
    elif until:
        del _lockouts[ip]
        _failed_attempts.pop(ip, None)


def _record_failure(ip: str) -> None:
    """Record a failed login and trigger lockout if threshold exceeded."""
    now = time.time()
    attempts = _failed_attempts[ip]
    attempts[:] = [t for t in attempts if now - t < LOCKOUT_WINDOW_SECS]
    attempts.append(now)
    logger.warning("Failed login from %s (attempt %d/%d in window)", ip, len(attempts), LOCKOUT_MAX_ATTEMPTS)
    if len(attempts) >= LOCKOUT_MAX_ATTEMPTS:
        _lockouts[ip] = now + LOCKOUT_DURATION_SECS
        _failed_attempts.pop(ip, None)
        logger.warning("IP %s locked out for %ds after %d failed attempts", ip, LOCKOUT_DURATION_SECS, LOCKOUT_MAX_ATTEMPTS)


def _clear_failures(ip: str) -> None:
    """Clear failure tracking on successful login."""
    _failed_attempts.pop(ip, None)
    _lockouts.pop(ip, None)


def _require_local_login_enabled(exempt_username: str | None = None) -> None:
    """ADR-0047 Step B guard. Raises 403 when an operator has explicitly
    retired local login (LOCAL_LOGIN_DISABLED=true) — false by default
    everywhere, including self-hosted/OSS installs and the public demo
    instance, so this is a no-op for them. Only the routes that MINT new
    local credentials call this; get_current_user's bearer-token
    VERIFICATION logic is left completely intact so this is a config
    flip, not a code deletion — see docs/adr/0047-*.md.

    ``exempt_username`` (ADR-0048) is the username this request has ALREADY
    established: the login body's username, or the ``sub`` claim of a
    refresh token whose signature has already been verified. Never a
    free-form client field. When it appears in SERVICE_ACCOUNT_USERNAMES the
    caller is let past THIS 403 and nothing else — the password check, the
    per-IP lockout, and the ``is_active`` check all still run, in the same
    order, on the same code path. There is no role or admin column on
    ``User`` (see app/models/user.py) for an allowlist entry to escalate.

    Omitting the argument is how a route says "no exemption exists here",
    and is the fail-closed default: ``setup`` and ``update_account`` CREATE
    and MUTATE credentials, so they stay hard-blocked even for an
    allowlisted name holding a valid token."""
    if not settings.local_login_disabled:
        return

    if exempt_username and exempt_username in settings.service_account_allowlist:
        logger.info(
            "ADR-0048: local-login gate bypassed for allowlisted service account '%s' "
            "— password, lockout and is_active checks still apply",
            exempt_username,
        )
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Local login is disabled on this instance — sign in via SSO",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SetupRequest(BaseModel):
    username: str
    password: str


@router.get("/setup-status")
async def setup_status(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns whether initial setup is needed, plus the
    two SSO flags the login screen needs BEFORE it can decide whether to
    attempt a silent Access probe or show the password form at all
    (ADR-0047). Both flags are safe to expose publicly: they say only
    whether an operator has wired Cloudflare Access, never a secret.

    Managed instances skip the setup wizard — admin is pre-created on startup.
    Local-login-disabled instances also skip it — there is no reason to run
    the wizard for a local account that could never be used to log in.
    """
    sso_configured = is_cf_access_configured()
    local_login_disabled = settings.local_login_disabled
    if settings.managed_instance or local_login_disabled:
        return {
            "needs_setup": False,
            "sso_configured": sso_configured,
            "local_login_disabled": local_login_disabled,
        }
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {
        "needs_setup": len(users) == 0,
        "sso_configured": sso_configured,
        "local_login_disabled": local_login_disabled,
    }


@router.post("/setup")
@limiter.limit("5/minute")
async def initial_setup(request: Request, body: SetupRequest, db: AsyncSession = Depends(get_db)):
    """Create the first admin user. Only works when no users exist."""
    # No exemption argument, by design (ADR-0048) — this route CREATES a
    # local credential. The allowlist buys a machine caller a login, never
    # a way to mint one.
    _require_local_login_enabled()
    client_ip = get_trusted_client_ip(request)
    result = await db.execute(select(User))
    existing = result.scalars().all()
    if len(existing) > 0:
        logger.warning("Setup attempt rejected — %d user(s) already exist (ip=%s)", len(existing), client_ip)
        raise HTTPException(status_code=403, detail="Setup already completed. Use login instead.")
    if not body.username or len(body.username.strip()) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    failures = check_password_complexity(body.password)
    if failures:
        raise HTTPException(status_code=400, detail=f"Password does not meet complexity requirements: {'; '.join(failures)}")
    new_hash = await hash_password_async(body.password)
    roundtrip_ok = await verify_password_async(body.password, new_hash)
    if not roundtrip_ok:
        logger.critical("SETUP: Bcrypt roundtrip FAILED for new admin user '%s' (ip=%s)", body.username, client_ip)
        raise HTTPException(status_code=500, detail="Password hashing failed — please retry")
    admin = User(username=body.username.strip(), hashed_password=new_hash)
    db.add(admin)
    await db.commit()
    logger.info("SETUP COMPLETE: Admin user '%s' created (ip=%s)", admin.username, client_ip)
    return {
        "status": "ok",
        "username": admin.username,
        "access_token": create_access_token({"sub": admin.username}),
        "refresh_token": create_refresh_token({"sub": admin.username}),
        "token_type": "bearer",
    }



# Login
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    # ADR-0048: the attempted username verbatim. `User.username == body.username`
    # below is a byte-exact varchar comparison, so stripping or case-folding
    # here would admit strings that lookup could never match. A name that is
    # not on the allowlist raises 403 right here, before the row is read, so
    # the response is identical whether or not that account exists.
    _require_local_login_enabled(body.username)
    client_ip = get_trusted_client_ip(request)
    logger.info("Login attempt: user='%s' ip=%s", body.username, client_ip)

    _check_lockout(client_ip)

    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if not user:
        logger.warning("Login failed: user '%s' not found (ip=%s)", body.username, client_ip)
        _record_failure(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        logger.warning("Login failed: user '%s' is deactivated (ip=%s)", body.username, client_ip)
        _record_failure(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    pw_ok = await verify_password_async(body.password, user.hashed_password)
    if not pw_ok:
        logger.warning(
            "Login failed: wrong password for user '%s' (ip=%s, hash_prefix=%s)",
            body.username,
            client_ip,
            user.hashed_password[:7] if user.hashed_password else "NONE",
        )
        _record_failure(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    _clear_failures(client_ip)

    logger.info("Login SUCCESS: user='%s' ip=%s", user.username, client_ip)

    return {
        "access_token": create_access_token({"sub": user.username}),
        "refresh_token": create_refresh_token({"sub": user.username}),
        "token_type": "bearer",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SSO -> bearer exchange (ADR-0048)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@router.post("/sso-exchange", response_model=TokenResponse)
@limiter.limit("10/minute")
async def sso_exchange(request: Request, db: AsyncSession = Depends(get_db)):
    """Trade a verified Cloudflare Access assertion for a normal local bearer
    pair — the missing half of ADR-0047, added by ADR-0048.

    WHY THIS EXISTS. Access is an *edge* control, and ADR-0047 Amendment 1
    established that three Access apps cover `droneops.barnardhq.com`, two of
    them with **bypass** policies over `/api/intake/*` and `/api/tos/*`. On a
    bypassed path the edge does not authenticate, so it injects no
    `Cf-Access-Jwt-Assertion` — and the 8 operator-only endpoints living
    under those prefixes therefore see no Access credential at all. An
    operator who authenticated purely through Access holds nothing those
    endpoints accept. Reordering the SPA's `useAuth.init()` can only preserve
    a local bearer that already exists; nothing could MINT one, because
    `POST /login` answers 403 once Step B is on. This route is that mint, and
    it is the reason Step B can be turned on without repeating the
    2026-09-21 onboarding outage.

    DELIBERATELY NOT BEHIND ``_require_local_login_enabled()``. This is not a
    local login — no password is presented, accepted, or created. It must
    work *precisely* when local login is disabled; gating it would reproduce
    the exact lockout ADR-0047 §Consequences warns about.

    TRUST BOUNDARY. The header's presence proves nothing: this origin is
    reachable from the WireGuard mesh (`10.99.0.4:8000`), where any container
    can set any header it likes. Only ``verify_cf_access_assertion`` makes it
    trustworthy — RS256 signature checked against Cloudflare's JWKS for this
    team domain, with an explicit algorithm allow-list (so `alg:none` and the
    HS256 confusion downgrade are refused before a key is tried), plus
    required `aud`, `iss` and `exp`, then the `CF_ACCESS_ALLOWED_EMAILS`
    allow-list. Every failure mode denies; none of them mints anything.

    STRUCTURALLY DARK WHEN UNCONFIGURED. With `CF_ACCESS_TEAM_DOMAIN` or
    `CF_ACCESS_AUD` empty — every self-hosted/OSS install and the public demo
    instance — this answers 404 before it so much as reads the header. It can
    never be a second way in for a deployment that has no Access in front
    of it.

    GRANTS NOTHING EXTRA. The identity comes from ``resolve_cf_access_user``,
    the same mapping-table resolver ``get_current_user`` already uses, so an
    exchange reaches exactly the shadow user an Access-authenticated request
    would have reached — no second identity mechanism, and no privilege to
    escalate (``users`` has no role column; see app/models/user.py). The
    response is byte-compatible with ``POST /login``: same claims, same
    helpers, same expiry, so nothing downstream in the SPA changes.
    """
    if not is_cf_access_configured():
        # 404, not 403: on an install with no Access the route should look
        # like it does not exist rather than advertise a door.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    result = await verify_cf_access_assertion(extract_cf_access_token(request))
    if not result.ok:
        # `result.reason` distinguishes `email_not_allowed` from
        # `token_expired`; that belongs in the log, never in the response.
        logger.warning("[CF-ACCESS] sso-exchange DENIED (reason=%s)", result.reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cloudflare Access assertion not valid for this instance",
        )

    user = await resolve_cf_access_user(db, result.email)
    logger.info("[CF-ACCESS] sso-exchange minted a bearer pair for email=%s", result.email)
    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Account management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@router.get("/account")
async def get_account(user: User = Depends(get_current_user)):
    """Get current account info."""
    return {
        "username": user.username,
    }


@router.put("/account")
async def update_account(
    body: AccountUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update username and/or password. Requires current password for verification."""
    # No exemption argument, by design (ADR-0048) — this route MUTATES a
    # credential. An allowlisted service account holding a valid bearer
    # token still gets 403 here.
    _require_local_login_enabled()
    if not await verify_password_async(body.current_password, user.hashed_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if not body.new_username and not body.new_password:
        raise HTTPException(status_code=400, detail="Nothing to update")

    old_username = user.username  # capture before any rename for cache invalidation
    if body.new_username and body.new_username != user.username:
        existing = await db.execute(select(User).where(User.username == body.new_username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")
        logger.info("Username change: '%s' -> '%s'", user.username, body.new_username)
        user.username = body.new_username

    if body.new_password:
        failures = check_password_complexity(body.new_password)
        if failures:
            raise HTTPException(
                status_code=400,
                detail=f"Password does not meet complexity requirements: {'; '.join(failures)}",
            )
        old_hash_prefix = user.hashed_password[:10] if user.hashed_password else "EMPTY"
        new_hash = await hash_password_async(body.new_password)
        user.hashed_password = new_hash
        logger.info(
            "PASSWORD CHANGE: user='%s' old_hash=%s... new_hash=%s...",
            user.username, old_hash_prefix, new_hash[:10],
        )

    # Explicit commit — do NOT rely on get_db cleanup
    await db.commit()

    # FIX-2 (v2.63.8): drop any cached User rows for the username(s) so
    # subsequent requests (with the new token) repopulate from DB. Both
    # the pre-rename and post-rename names are invalidated to cover the
    # username-change path. Token-prefix-keyed cache + new token mean
    # this is belt-and-suspenders, but cheap and correct.
    invalidate_user_cache(old_username)
    if body.new_username and body.new_username != old_username:
        invalidate_user_cache(body.new_username)

    # Read-back verification: re-query the database to confirm the write stuck
    if body.new_password:
        verify_result = await db.execute(select(User).where(User.username == user.username))
        saved_user = verify_result.scalar_one_or_none()
        if saved_user:
            readback_ok = await verify_password_async(body.new_password, saved_user.hashed_password)
            logger.info(
                "PASSWORD VERIFY: user='%s' readback_ok=%s saved_hash=%s...",
                user.username, readback_ok, saved_user.hashed_password[:10],
            )
            if not readback_ok:
                logger.critical(
                    "PASSWORD WRITE FAILED: hash in DB does not match new password! "
                    "user='%s' expected_hash=%s... got_hash=%s...",
                    user.username, new_hash[:10],
                    saved_user.hashed_password[:10] if saved_user.hashed_password else "EMPTY",
                )
                raise HTTPException(
                    status_code=500,
                    detail="Password save failed — please try again",
                )
        else:
            logger.critical("PASSWORD VERIFY: user '%s' not found after commit!", user.username)

    return {
        "status": "ok",
        "username": user.username,
        "access_token": create_access_token({"sub": user.username}),
        "refresh_token": create_refresh_token({"sub": user.username}),
    }


@router.get("/password-rules")
async def get_password_rules():
    """Return the current password complexity rules (for frontend display)."""
    return {
        "rules": [desc for desc, _ in PASSWORD_RULES],
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(
            request.refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        username: str = payload.get("sub")
        token_type: str = payload.get("type")
        if username is None or token_type != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # ADR-0048: gated on the SIGNED `sub` claim decoded above — never on a
    # client-supplied field — and placed before the user lookup so a
    # non-allowlisted subject gets the same 403 whether or not the row
    # exists. This guard ran as the handler's first statement under
    # ADR-0047; it cannot any more, because it now needs a subject that
    # only the decode can establish. The one deliberate consequence: with
    # local login disabled, an UNDECODABLE refresh token now answers 401
    # instead of 403. That reveals nothing about any user (the token failed
    # signature verification) and `GET /api/auth/setup-status` already
    # publishes the flag itself. Note this is NOT "the flag has never been
    # on": ADR-0047 Amendment 2 shows it was true in production for ~6 h on
    # 2026-09-21. What is unchanged is every path that carries a decodable
    # token, which is every path a real caller takes.
    _require_local_login_enabled(username)

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    logger.info("Token refreshed for user '%s'", user.username)
    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Auth diagnostics — GET /api/auth/diag (no auth required)
# Checks bcrypt, database connectivity, and user status.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@router.get("/diag")
async def auth_diagnostics(db: AsyncSession = Depends(get_db)):
    """Public diagnostic endpoint — checks auth system health."""
    import bcrypt as _bcrypt

    diag = {
        "bcrypt_version": _bcrypt.__version__,
        "bcrypt_roundtrip": False,
        "user_count": 0,
        "needs_setup": True,
        "lockouts_active": len(_lockouts),
        "failed_attempt_ips": len(_failed_attempts),
    }

    # Test bcrypt roundtrip
    try:
        test_pw = "DiagTest123!@#"
        test_hash = await hash_password_async(test_pw)
        diag["bcrypt_roundtrip"] = await verify_password_async(test_pw, test_hash)
    except Exception as exc:
        diag["bcrypt_error"] = str(exc)

    # Check users in DB
    try:
        result = await db.execute(select(User))
        users = result.scalars().all()
        diag["user_count"] = len(users)
        diag["needs_setup"] = len(users) == 0
        if users:
            diag["users"] = [{"username": u.username, "is_active": u.is_active, "hash_valid": bool(u.hashed_password and u.hashed_password.startswith("$2b$") and len(u.hashed_password) == 60)} for u in users]
    except Exception as exc:
        diag["db_error"] = str(exc)

    logger.info("Auth diagnostics: %s", diag)
    return diag
