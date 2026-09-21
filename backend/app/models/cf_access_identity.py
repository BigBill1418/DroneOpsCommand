"""Cloudflare Access identity -> local ``users.id`` mapping (ADR-0047).

Populated ONLY by ``app.auth.jwt.resolve_cf_access_user`` — never by
matching ``users.username`` against an Access-verified email. This is the
fix for the exact failure a security review caught in the marketing pilot's
first draft (marketing ADR-0099, correction entry, 2026-09-21): a
username-match lookup can silently adopt a pre-existing local account, and
a hardcoded ``role='admin'`` grant on first Access login bypasses the
operator's own account state entirely.

DroneOpsCommand has no ``role`` column on ``users`` at all (verified: it is
a single-operator tool — no per-user data partitioning exists anywhere in
this schema, confirmed by grepping for ``ForeignKey("users.id")`` across
``app/models/`` and finding zero matches other than this table), so the
"auto-granted admin" half of that incident does not apply here. The
"silently adopts a pre-existing account by username match" half does, and
this table is the fix: a CF-Access-authenticated request can only ever
resolve to a ``users`` row this mapping itself points at — it can adopt
nothing pre-existing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CfAccessIdentity(Base):
    """One row per Access-verified email that has ever authenticated."""

    __tablename__ = "cf_access_identities"

    # The Access-verified email is the natural key — lower-cased before
    # every insert/lookup (app.auth.jwt), so this column does not need
    # citext or a functional index to behave case-insensitively.
    email: Mapped[str] = mapped_column(primary_key=True)

    # One shadow `users` row per mapping. UNIQUE so the relationship really
    # is 1:1 and a race can be detected (IntegrityError) rather than
    # silently producing two emails pointing at one user.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
