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
    verify_password,
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


# ── Login lockout: 3 failed attempts in 120s → locked for 5 minutes ──
LOCKOUT_MAX_ATTEMPTS = 3
LOCKOUT_WINDOW_SECS = 120
LOCKOUT_DURATION_SECS = 300

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
        # Lockout expired — clean up
        del _lockouts[ip]
        _failed_attempts.pop(ip, None)


def _record_failure(ip: str) -> None:
    """Record a failed login and trigger lockout if threshold exceeded."""
    now = time.time()
    attempts = _failed_attempts[ip]
    # Prune attempts outside the window
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


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    client_ip = get_remote_address(request)
    _check_lockout(client_ip)

    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        _record_failure(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    _clear_failures(client_ip)
    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
    )


@router.get("/account")
async def get_account(user: User = Depends(get_current_user)):
    """Get current account info."""
    return {"username": user.username}


@router.put("/account")
async def update_account(
    body: AccountUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update username and/or password. Requires current password for verification."""
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if not body.new_username and not body.new_password:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if body.new_username and body.new_username != user.username:
        existing = await db.execute(select(User).where(User.username == body.new_username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")
        user.username = body.new_username

    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        user.hashed_password = hash_password(body.new_password)

    await db.flush()

    # Return fresh tokens with (potentially new) username
    return {
        "status": "ok",
        "username": user.username,
        "access_token": create_access_token({"sub": user.username}),
        "refresh_token": create_refresh_token({"sub": user.username}),
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

    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
    )
