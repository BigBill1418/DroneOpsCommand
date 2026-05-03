"""Active TOS template loader.

The repo has no ``settings_service`` abstraction — the existing intake
router (``backend/app/routers/intake.py``) reads the TOS PDF straight
from disk under ``${UPLOAD_DIR}/tos/``. We mirror that storage choice
here so the new ``/api/tos/*`` routes and the long-lived
``/api/intake/*`` routes share the exact same on-disk artifact (no
duplicated upload UI, no parallel store, no drift).

Per-customer override:
    ``${UPLOAD_DIR}/tos/tos_<customer.id>.pdf``  →  customer-specific
Default:
    ``${UPLOAD_DIR}/tos/default_tos.pdf``        →  Rev 3 template

Customer-specific takes precedence iff a ``customer_id`` is supplied
*and* the file exists. Both code paths are exercised by the existing
intake flow today, so no compat surprises.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger("doc.tos.template")

# Stable version label baked into the row's ``template_version`` column.
# Bump the suffix when you change the on-disk Rev. The hash is the
# load-bearing anchor; this string is just an operator-friendly label.
DEFAULT_TEMPLATE_VERSION = "DOC-001/TOS/REV3"


@dataclass(frozen=True)
class ActiveTosTemplate:
    """Resolved template bytes + the version label to persist."""

    bytes: bytes
    version: str
    source_path: str


def _tos_dir() -> Path:
    return Path(settings.upload_dir) / "tos"


def _default_path() -> Path:
    return _tos_dir() / "default_tos.pdf"


def _customer_path(customer_id: uuid.UUID) -> Path:
    return _tos_dir() / f"tos_{customer_id}.pdf"


def get_active_tos_template(
    customer_id: uuid.UUID | None = None,
) -> ActiveTosTemplate | None:
    """Load the active TOS PDF bytes.

    Returns ``None`` if no template is configured (so the route can
    surface a 404/503 cleanly instead of crashing on a missing file).
    """
    if customer_id is not None:
        cust_path = _customer_path(customer_id)
        if cust_path.is_file():
            logger.debug("[TOS-TEMPLATE] Serving customer-specific path=%s", cust_path)
            return ActiveTosTemplate(
                bytes=cust_path.read_bytes(),
                version=DEFAULT_TEMPLATE_VERSION,
                source_path=str(cust_path),
            )

    default_path = _default_path()
    if default_path.is_file():
        logger.debug("[TOS-TEMPLATE] Serving default path=%s", default_path)
        return ActiveTosTemplate(
            bytes=default_path.read_bytes(),
            version=DEFAULT_TEMPLATE_VERSION,
            source_path=str(default_path),
        )

    logger.warning("[TOS-TEMPLATE] No TOS template configured (looked under %s)", _tos_dir())
    return None


def signed_pdf_dir() -> Path:
    """Directory where signed PDFs land. Idempotent — created lazily.

    Uses the same ``upload_dir`` Docker volume so the PDFs are picked
    up by the existing backup + replication jobs without any new
    plumbing.
    """
    p = Path(settings.upload_dir) / "tos_signed"
    p.mkdir(parents=True, exist_ok=True)
    # mode is whatever the parent volume allows; we do not chmod so we
    # don't accidentally widen the group bit on a hardened mount.
    return p
