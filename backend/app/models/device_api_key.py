"""Device API key model — allows field controllers to authenticate without a user login.

ADR-0003 (2026-04-24): zero-touch key rotation. Two grace-window columns added
(``rotated_to_key_hash`` + ``rotation_grace_until``). Both nullable; NULL =
no rotation in flight. During the grace window both ``key_hash`` and
``rotated_to_key_hash`` authenticate; after the window expires the Celery
finalizer promotes the new hash and clears the grace columns.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeviceApiKey(Base):
    __tablename__ = "device_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 hex digest of the raw key — the raw key is never stored
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── ADR-0003 — zero-touch rotation grace window ──────────────────────
    # ``rotated_to_key_hash`` holds the SHA-256 of the new raw key while the
    # rotation is in flight. ``rotation_grace_until`` is the UTC instant the
    # grace window closes. Both are NULL outside grace. The raw new key is
    # never stored in this table — it lives transiently in Redis under
    # ``doc:rotation:hint:{device_id}`` so the device-health endpoint can
    # return it once to the OLD-key-authenticated request.
    rotated_to_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rotation_grace_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
