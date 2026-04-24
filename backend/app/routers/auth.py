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
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    hash_password_async,
    invalidate_user_cache,
    verify_password,
    verify_password_async,
    check_password_complexity,
    PASSWORD_RULES,
)
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse

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
limiter = Limiter(key_func=get_remote_address)


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SetupRequest(BaseModel):
    username: str
    password: str


@router.get("/setup-status")
async def setup_status(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns whether initial setup is needed.

    Managed instances skip the setup wizard — admin is pre-created on startup.
    """
    if settings.managed_instance:
        return {"needs_setup": False}
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {"needs_setup": len(users) == 0}


@router.post("/setup")
@limiter.limit("5/minute")
async def initial_setup(request: Request, body: SetupRequest, db: AsyncSession = Depends(get_db)):
    """Create the first admin user. Only works when no users exist."""
    client_ip = get_remote_address(request)
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
    client_ip = get_remote_address(request)
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
