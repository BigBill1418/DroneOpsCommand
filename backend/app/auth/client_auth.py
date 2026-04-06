"""Client portal JWT authentication — separate from operator auth.

Client tokens carry type=client_access and scope=list of mission IDs.
Operator tokens are rejected at client endpoints and vice versa.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.client_portal import ClientAccessToken
from app.models.customer import Customer

logger = logging.getLogger("doc.client_portal.auth")

security = HTTPBearer()


def hash_token(token: str) -> str:
    """SHA-256 hash of a token for storage lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_client_token(customer_id: UUID, mission_ids: list[str], expires_days: int = 30) -> str:
    """Create a JWT for client portal access.

    Claims:
        sub: customer UUID
        type: client_access (distinguishes from operator tokens)
        scope: list of "mission:<uuid>" strings
        exp: expiry timestamp
    """
    scope = [f"mission:{mid}" for mid in mission_ids]
    expire = datetime.utcnow() + timedelta(days=expires_days)
    payload = {
        "sub": str(customer_id),
        "type": "client_access",
        "scope": scope,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    logger.info(
        "[CLIENT-AUTH] Created token for customer=%s, missions=%d, expires=%s",
        customer_id, len(mission_ids), expire.isoformat(),
    )
    return token


class ClientContext:
    """Decoded client auth context injected into protected endpoints."""

    def __init__(self, customer_id: UUID, mission_ids: list[str], customer: Customer):
        self.customer_id = customer_id
        self.mission_ids = mission_ids
        self.customer = customer

    def can_access_mission(self, mission_id: str) -> bool:
        return str(mission_id) in self.mission_ids


async def get_current_client(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> ClientContext:
    """FastAPI dependency — decode client JWT and return ClientContext.

    Rejects operator tokens (type != client_access).
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        customer_id_str: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")

        if not customer_id_str or token_type != "client_access":
            logger.warning("[CLIENT-AUTH] Rejected token: type=%s sub=%s", token_type, customer_id_str)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid client token",
            )

        scope: list[str] = payload.get("scope", [])
        mission_ids = [s.replace("mission:", "") for s in scope if s.startswith("mission:")]
        customer_id = UUID(customer_id_str)

    except JWTError as exc:
        logger.warning("[CLIENT-AUTH] JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired client token",
        )

    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        logger.warning("[CLIENT-AUTH] Customer not found: %s", customer_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
        )

    logger.debug("[CLIENT-AUTH] Authenticated customer=%s, missions=%s", customer_id, mission_ids)
    return ClientContext(customer_id=customer_id, mission_ids=mission_ids, customer=customer)
