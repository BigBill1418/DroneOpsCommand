from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == request.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
    )


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
