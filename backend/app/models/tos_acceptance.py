"""SQLAlchemy 2.0 model for the ``tos_acceptances`` audit table.

Every row is the immutable record of a single customer acceptance of a
specific version of the BarnardHQ Terms of Service. Two SHA-256 hashes
are the load-bearing integrity anchors:

* ``template_sha256`` — pins the exact unsigned template the customer
  saw and agreed to. Bumping the uploaded TOS file produces a new
  ``template_sha256`` on subsequent rows; old rows still reference
  the old template by hash.
* ``signed_sha256`` — over the post-fill bytes; one-line verification
  is ``hashlib.sha256(open(signed_pdf_path, 'rb').read()).hexdigest()
  == row.signed_sha256``.

The table is created by ``Base.metadata.create_all`` on the next
container boot — this repo does not use Alembic (per CLAUDE.md
deployment topology + ``backend/app/main.py:_add_missing_columns``
pattern). All columns here are non-nullable except ``customer_id`` and
``intake_token`` so create_all + a fresh table cannot land in a
partial state.

ADR-0010.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import INET, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TosAcceptance(Base):
    """One row per accepted Terms-of-Service signing event."""

    __tablename__ = "tos_acceptances"

    # Surrogate primary key. server_default ``gen_random_uuid()`` is
    # provided by the pgcrypto extension which DroneOps' postgres image
    # already enables (other tables use uuid.uuid4 client-side; we set
    # both so create_all on a fresh DB lands a sensible default and the
    # ORM can pre-populate when explicit).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Human-readable, time-sortable audit identifier
    # (DOC-YYYYMMDDHHMMSS-<8 hex>). Unique so a duplicate accidental
    # POST is rejected at the DB layer rather than producing two rows.
    audit_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)

    # Soft link to the customer profile, if known. Nullable because
    # the new /tos/accept page can be reached either with both ``token``
    # and ``customer_id`` (the intake-email path) or anonymously (a
    # cold visitor signing the public TOS). FK ``ON DELETE SET NULL``
    # preserves the audit row even if the customer is deleted.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The intake token the customer used (if any) — gives the
    # customer a token-only download path
    # (``/api/tos/signed/by-token/{intake_token}``) for as long as
    # their token has not expired.
    intake_token: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    # Identity actually filled into the AcroForm fields. Stored even
    # though the same values are also embedded in ``signed_pdf_path``
    # so an operator can run ``SELECT … FROM tos_acceptances`` without
    # opening every PDF.
    client_name:    Mapped[str] = mapped_column(Text, nullable=False)
    client_email:   Mapped[str] = mapped_column(Text, nullable=False, index=True)
    client_company: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_title:   Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Server-observed context at the moment of acceptance.
    client_ip:   Mapped[str]      = mapped_column(INET, nullable=False)
    user_agent:  Mapped[str]      = mapped_column(Text, nullable=False, server_default="")
    accepted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    # Document-version anchor and tamper-detection anchor. Both 64-char
    # lowercase hex SHA-256 digests.
    template_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="DOC-001/TOS/REV3"
    )
    template_sha256:  Mapped[str] = mapped_column(Text, nullable=False, index=True)
    signed_sha256:    Mapped[str] = mapped_column(Text, nullable=False)

    # Where the signed PDF lives on disk. Path under the ``app_data``
    # Docker volume so backups and replication catch it.
    signed_pdf_path:  Mapped[str] = mapped_column(Text, nullable=False)
    signed_pdf_size:  Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Eager-load the customer when present so the operator-side
    # download endpoint can render a human label without a second
    # query. ``viewonly`` would be tempting but the relationship is
    # 1:N and the FK ON DELETE SET NULL handles deletes cleanly.
    customer = relationship("Customer", lazy="joined")
