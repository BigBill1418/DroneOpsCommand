"""TOS acceptance helper — AcroForm fill + lock + SHA-256 anchor.

Replaces the canvas-signature TOS pattern with a typed-name + checkbox
flow that fills seven named AcroForm fields on the BarnardHQ Rev 3 ToS
template, locks each filled field read-only (Ff bit 1), and SHA-256
hashes both the pre-fill template bytes and the post-fill signed bytes
to anchor the document version and detect tampering.

ADR-0010. The seven required fields are:

    client_name, client_email, client_company, client_title,
    signed_at_utc, client_ip, audit_id

The ``audit_id`` is generated server-side as ``DOC-<YYYYMMDDHHMMSS>-<8hex>``
so it is unique, time-sortable, and correlatable with the row stored in
the ``tos_acceptances`` table.

This module is deliberately sync — the heavy lifting is bytes-in /
bytes-out PDF mutation. FastAPI runs it in a threadpool when called
from an async route, so no event-loop work is blocked.
"""

from __future__ import annotations

import hashlib
import io
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject, NumberObject

logger = logging.getLogger("doc.tos")

# Required AcroForm field names on the BarnardHQ Rev 3 template.
# Order matches the on-page layout (last page of the PDF).
REQUIRED_FIELDS: tuple[str, ...] = (
    "client_name",
    "client_email",
    "client_company",
    "client_title",
    "signed_at_utc",
    "client_ip",
    "audit_id",
)

# /Ff bit 1 = ReadOnly (PDF 1.7 §12.7.3.1, table 222). Setting this on a
# filled field prevents downstream PDF readers from offering the form
# field as editable, so the typed values cannot be changed without
# breaking the saved signed_sha256.
_FF_READONLY = 1


# ── Data containers ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ClientIdentity:
    """Identity supplied by the customer on the acceptance form.

    All four fields are stored verbatim into the AcroForm. ``email`` is
    normalised (trim + lowercase) before hashing so two acceptances of
    the same address produce the same field-value but a fresh
    audit/signed hash (the ``audit_id`` and timestamp differ).
    """

    full_name: str
    email: str
    company: str = ""
    title: str = ""


@dataclass(frozen=True)
class AcceptanceContext:
    """Server-observed context at the moment of acceptance."""

    ip: str
    user_agent: str
    accepted_at: datetime  # tz-aware UTC


@dataclass(frozen=True)
class AcceptanceRecord:
    """Audit-trail record returned alongside the signed PDF bytes.

    Persisted into ``tos_acceptances`` by the route layer. The two
    hashes are the load-bearing integrity anchors — ``template_sha256``
    pins the version of the unsigned template the customer agreed to,
    and ``signed_sha256`` allows tamper-detection round-trip on the
    stored signed PDF (``hashlib.sha256(pdf_bytes).hexdigest() ==
    record.signed_sha256``).
    """

    audit_id: str
    client: ClientIdentity
    context: AcceptanceContext
    template_sha256: str
    signed_sha256: str
    field_values: dict[str, str] = field(default_factory=dict)


# ── Public API ───────────────────────────────────────────────────────


def template_has_required_fields(pdf_bytes: bytes) -> bool:
    """Return True iff the PDF has all seven required AcroForm fields.

    Used by the Settings upload endpoint to reject any PDF that is not
    the BarnardHQ Rev 3 template (or a derivative carrying the same
    field names). Defends against silently configuring a TOS that
    cannot be filled.
    """
    if not pdf_bytes:
        return False
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        fields = reader.get_fields() or {}
    except Exception as exc:  # malformed PDF, encrypted, etc.
        logger.warning("template_has_required_fields: PDF parse failed: %s", exc)
        return False
    present = set(fields.keys())
    missing = [f for f in REQUIRED_FIELDS if f not in present]
    if missing:
        logger.info("template_has_required_fields: missing fields %s", missing)
        return False
    return True


def accept_tos(
    client: ClientIdentity,
    context: AcceptanceContext,
    *,
    template_bytes: bytes | None = None,
) -> tuple[bytes, AcceptanceRecord]:
    """Fill, lock, and hash the TOS template.

    Returns ``(signed_pdf_bytes, record)``. Raises ``ValueError`` if
    ``template_bytes`` is missing or empty so a caller-level
    misconfiguration (no TOS uploaded yet) surfaces immediately rather
    than silently producing an unsigned PDF.

    Algorithm:

    1. Generate ``audit_id`` (``DOC-<utc-stamp>-<8 hex>``).
    2. Compose the seven field values from the inputs (email is
       trimmed/lower-cased; timestamp is rendered ISO-8601 in UTC with
       a trailing ``Z``).
    3. SHA-256 the template bytes (``template_sha256``).
    4. Walk the PDF, fill every matching field, set its ``/Ff``
       ReadOnly bit, and remove ``/AP`` (any cached appearance stream)
       so PDF viewers regenerate the visual from the new ``/V`` value.
    5. SHA-256 the resulting bytes (``signed_sha256``).
    """
    if not template_bytes:
        raise ValueError("TOS template_bytes is empty — no template configured")

    audit_id = _generate_audit_id(context.accepted_at)
    field_values = _build_field_values(client, context, audit_id)
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()

    signed_bytes = _fill_and_lock(template_bytes, field_values)
    signed_sha256 = hashlib.sha256(signed_bytes).hexdigest()

    logger.info(
        "[TOS-ACCEPT] audit_id=%s email=%s ip=%s template_sha=%s signed_sha=%s "
        "template_size=%d signed_size=%d",
        audit_id,
        field_values["client_email"],
        field_values["client_ip"],
        template_sha256[:12],
        signed_sha256[:12],
        len(template_bytes),
        len(signed_bytes),
    )

    record = AcceptanceRecord(
        audit_id=audit_id,
        client=client,
        context=context,
        template_sha256=template_sha256,
        signed_sha256=signed_sha256,
        field_values=field_values,
    )
    return signed_bytes, record


def verify_signed_tos(signed_pdf_bytes: bytes, expected_sha256: str) -> bool:
    """One-line tamper-detection round-trip.

    Re-hashes the stored bytes and compares using a constant-time
    comparison so the routine cannot be turned into a hash oracle.
    """
    actual = hashlib.sha256(signed_pdf_bytes).hexdigest()
    return secrets.compare_digest(actual, expected_sha256)


# ── Internals ────────────────────────────────────────────────────────


def _generate_audit_id(accepted_at: datetime) -> str:
    """Time-sortable, collision-resistant audit identifier.

    Format: ``DOC-YYYYMMDDHHMMSS-<8 hex>``. The timestamp is the
    customer's reported acceptance moment (tz-normalised to UTC) so
    the audit_id sorts in the same order rows land in the table.
    """
    ts = accepted_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"DOC-{ts}-{secrets.token_hex(4)}"


def _build_field_values(
    client: ClientIdentity,
    context: AcceptanceContext,
    audit_id: str,
) -> dict[str, str]:
    """Materialise the seven field values that go into the PDF."""
    return {
        "client_name":   (client.full_name or "").strip(),
        "client_email":  (client.email or "").strip().lower(),
        "client_company": (client.company or "").strip(),
        "client_title":  (client.title or "").strip(),
        "signed_at_utc": context.accepted_at.astimezone(timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client_ip":     (context.ip or "").strip(),
        "audit_id":      audit_id,
    }


def _fill_and_lock(template_bytes: bytes, values: dict[str, str]) -> bytes:
    """Fill matching AcroForm fields and lock them ReadOnly.

    Why this is more than ``writer.update_page_form_field_values``:
    the default helper writes the value but does not flip the field's
    ``/Ff`` ReadOnly bit, which means a downstream Acrobat could
    overwrite the customer's typed name and the bytes-on-disk would
    no longer match the signed_sha256 we hashed. We flip the bit on
    every filled annotation, and clear ``/AP`` so PDF viewers
    re-render the appearance from the new ``/V``.

    Also sets ``/NeedAppearances=true`` on the AcroForm root so any
    consumer that *does* honour it regenerates appearances on first
    open — this is the spec-recommended pattern for filled forms.
    """
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter(clone_from=reader)

    # Tell readers to regenerate field appearances from the /V values.
    if "/AcroForm" in writer._root_object:
        acroform = writer._root_object["/AcroForm"]
        acroform.update({NameObject("/NeedAppearances"): BooleanObject(True)})

    for page in writer.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = annot_ref.get_object()
            field_name = annot.get("/T")
            if field_name is None or str(field_name) not in values:
                continue
            value = values[str(field_name)]
            annot.update(
                {
                    NameObject("/V"): _pdf_string(value),
                    # Lock the field. Preserve any existing flags
                    # (e.g. /Ff bit 13 = Multiline on a free-form box)
                    # by OR-ing rather than overwriting.
                    NameObject("/Ff"): NumberObject(
                        int(annot.get("/Ff", 0)) | _FF_READONLY,
                    ),
                }
            )
            # Drop any cached appearance so the new /V renders.
            if "/AP" in annot:
                del annot["/AP"]

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _pdf_string(value: str):
    """Wrap a Python string in a pypdf TextStringObject.

    Imported lazily because pypdf's generic module surface has shifted
    across releases — this isolates the import to one place.
    """
    from pypdf.generic import TextStringObject

    return TextStringObject(value)
