# Claude Code Handoff — DOC TOS Acceptance: Canvas → AcroForm Replacement

**Repo:** `BigBill1418/DroneOpsCommand`
**Target version:** v2.63.x → v2.64.0
**Primary deployment:** BOS-HQ (image built by BOS-HQ self-hosted GH Actions runner per ADR-0029)
**Branch:** `feat/tos-acroform-acceptance`
**ADR:** Land as ADR-0030

---

## Mission

Rip the canvas-signature TOS widget out of DOC. Replace it with a typed-name + checkbox + AcroForm-fill flow against the BarnardHQ Rev 3 ToS PDF. Each acceptance produces one self-contained signed PDF + one audit row, anchored by SHA-256 hashes of the template and the signed output.

**Replaces** the TOS-specific signature widget only. **Do NOT touch** the deliverable-approval `signatures` table — that's the customer signing off on completed mission deliverables, separate concern, stays exactly as it is.

---

## Phase 0 — Discovery (always run first)

Before writing any code, find the existing TOS code in the repo. Run these against `~/droneops` (or wherever DOC lives locally):

```bash
# Find the canvas signature widget — note the exact paths for removal in Phase 5
grep -rn -E "SignatureCanvas|SignaturePad|signature_pad|drawSignature|react-signature" \
  frontend/src backend/app

# Find the existing TOS upload + acceptance code in the backend
grep -rn -E "tos_template|tos_document|default_tos|tos_signature|tos_accept" \
  backend/app

# Find the existing TOS storage — file path in settings? Or BLOB in DB?
grep -rn -E "tos.*\.pdf|TosTemplate|tos_pdf" backend/app

# Confirm pypdf is already in the dependency tree (DOC uses it for log parsing)
grep -E "^pypdf" backend/requirements*.txt

# Find Alembic head — you'll need this for the migration's down_revision
cd backend && alembic heads

# Find the existing intake token flow — the new TOS page must be reachable via the same token
grep -rn -E "intake_token|customer_intake|intake_link" backend/app frontend/src
```

Capture the results. They drive these later decisions:

| Discovery question | Use it for |
|---|---|
| Is the existing TOS template on disk or BLOB? | Body of `get_active_tos_template()` in Phase 2 |
| What is the head Alembic revision? | `down_revision` in the migration file |
| Where does the canvas widget live? | Files to delete in Phase 5 |
| What's the intake token URL pattern? | New React page route in Phase 3 |
| Is `pypdf>=5.0` in requirements? | Add it if not |
| Is `email-validator` in requirements? | Required by Pydantic v2 `EmailStr`; add if missing |

---

## Phase 1 — Database

### 1.1  Migration

**File:** `backend/alembic/versions/0030_tos_acceptances.py`

```python
"""tos_acceptances — AcroForm-fill replacement for canvas TOS signatures

Revision ID: 0030_tos_acceptances
Revises: <REPLACE WITH alembic heads OUTPUT>
Create Date: 2026-XX-XX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, INET, TIMESTAMP

revision = "0030_tos_acceptances"
down_revision = "<REPLACE>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "tos_acceptances",
        sa.Column("id", UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("audit_id", sa.Text(), nullable=False, unique=True),
        sa.Column("customer_id", sa.Integer(),
                  sa.ForeignKey("customers.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("intake_token", sa.Text(), nullable=True, index=True),

        sa.Column("client_name",    sa.Text(), nullable=False),
        sa.Column("client_email",   sa.Text(), nullable=False),
        sa.Column("client_company", sa.Text(), nullable=False, server_default=""),
        sa.Column("client_title",   sa.Text(), nullable=False, server_default=""),

        sa.Column("client_ip",   INET(),    nullable=False),
        sa.Column("user_agent",  sa.Text(), nullable=False, server_default=""),
        sa.Column("accepted_at", TIMESTAMP(timezone=True), nullable=False),

        sa.Column("template_version",  sa.Text(), nullable=False,
                  server_default="DOC-001/TOS/REV3"),
        sa.Column("template_sha256",   sa.Text(), nullable=False),
        sa.Column("signed_sha256",     sa.Text(), nullable=False),
        sa.Column("signed_pdf_path",   sa.Text(), nullable=False),
        sa.Column("signed_pdf_size",   sa.Integer(), nullable=False),

        sa.Column("created_at", TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_tos_acceptances_email", "tos_acceptances", ["client_email"])
    op.create_index("ix_tos_acceptances_template_sha", "tos_acceptances", ["template_sha256"])
    op.create_index("ix_tos_acceptances_accepted_at", "tos_acceptances", ["accepted_at"])


def downgrade() -> None:
    op.drop_index("ix_tos_acceptances_accepted_at", "tos_acceptances")
    op.drop_index("ix_tos_acceptances_template_sha", "tos_acceptances")
    op.drop_index("ix_tos_acceptances_email",       "tos_acceptances")
    op.drop_table("tos_acceptances")
```

### 1.2  Existing TOS-acceptance data

If Phase 0 discovered an existing TOS acceptance table (likely named `tos_signatures`, `customer_tos_acceptances`, or a column on `customers` like `tos_signed_at`):

- **Do NOT migrate the data into the new table.** The old canvas-signed records have no AcroForm-field analog and no template/signed SHA-256. Forcing them into the new schema would lie about their integrity.
- **Leave the old table in place** as a historical archive. Mark it read-only by application convention (no new writes from the new code path).
- **Document the cut-over date** in ADR-0030 so future readers know which table to query for which time period.

If Phase 0 found that TOS acceptance data is currently stored as columns on `customers` (e.g. `tos_signature_data`, `tos_signed_at`, `tos_signed_ip`), leave the columns in place but stop writing to them.

---

## Phase 2 — Backend

### 2.1  Drop in the helper module verbatim

**File:** `backend/app/services/tos_acceptance.py` — copy `tos_acceptance.py` from this delivery exactly. No edits needed.

### 2.2  Ensure dependencies

**File:** `backend/requirements.txt` — confirm or add:

```
pypdf>=5.0
email-validator>=2.0
```

### 2.3  SQLAlchemy 2.0 model

**File:** `backend/app/models/tos_acceptance.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import Integer, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, INET, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.customer import Customer  # adjust import to actual path


class TosAcceptance(Base):
    __tablename__ = "tos_acceptances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    audit_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    intake_token: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    client_name:    Mapped[str] = mapped_column(Text, nullable=False)
    client_email:   Mapped[str] = mapped_column(Text, nullable=False, index=True)
    client_company: Mapped[str] = mapped_column(Text, nullable=False, default="")
    client_title:   Mapped[str] = mapped_column(Text, nullable=False, default="")

    client_ip:   Mapped[str]      = mapped_column(INET, nullable=False)
    user_agent:  Mapped[str]      = mapped_column(Text, nullable=False, default="")
    accepted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    template_version: Mapped[str] = mapped_column(Text, nullable=False,
                                                  default="DOC-001/TOS/REV3")
    template_sha256:  Mapped[str] = mapped_column(Text, nullable=False, index=True)
    signed_sha256:    Mapped[str] = mapped_column(Text, nullable=False)
    signed_pdf_path:  Mapped[str] = mapped_column(Text, nullable=False)
    signed_pdf_size:  Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    customer: Mapped[Customer | None] = relationship("Customer", lazy="joined")
```

### 2.4  Pydantic v2 schemas

**File:** `backend/app/schemas/tos_acceptance.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class TosAcceptanceRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email:     EmailStr
    company:   str  = Field(default="", max_length=120)
    title:     str  = Field(default="", max_length=80)
    confirm:   bool = Field(description="Explicit checkbox — must be True")

    customer_id:  int | None = None
    intake_token: str | None = None

    @field_validator("confirm")
    @classmethod
    def must_confirm(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Acceptance checkbox must be checked")
        return v


class TosAcceptanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               uuid.UUID
    audit_id:         str
    accepted_at:      datetime
    template_version: str
    template_sha256:  str
    signed_sha256:    str
    download_url:     str
```

### 2.5  Template loader

The route below depends on `get_active_tos_template()`. Phase 0 told you whether DOC stores the operator-uploaded TOS as a filesystem path or a DB BLOB. Implement the loader against the existing storage — do NOT build a parallel store. Both common shapes:

**If the existing settings store the TOS as a filesystem path** (likely under `app_data` Docker volume):

```python
# backend/app/services/tos_template.py
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting  # adjust to actual API


@dataclass(frozen=True)
class ActiveTosTemplate:
    bytes: bytes
    version: str


async def get_active_tos_template(db: AsyncSession) -> ActiveTosTemplate | None:
    path_setting = await get_setting(db, "tos_template_path")
    version      = await get_setting(db, "tos_template_version") or "DOC-001/TOS/REV3"
    if not path_setting:
        return None
    p = Path(path_setting)
    if not p.is_file():
        return None
    return ActiveTosTemplate(bytes=p.read_bytes(), version=version)
```

**If the existing settings store the TOS as a binary BLOB:**

```python
async def get_active_tos_template(db: AsyncSession) -> ActiveTosTemplate | None:
    blob    = await get_setting_bytes(db, "tos_template_blob")
    version = await get_setting(db, "tos_template_version") or "DOC-001/TOS/REV3"
    if not blob:
        return None
    return ActiveTosTemplate(bytes=blob, version=version)
```

### 2.6  Route module

**File:** `backend/app/routes/tos.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.tos_acceptance import TosAcceptance
from app.schemas.tos_acceptance import (
    TosAcceptanceRequest, TosAcceptanceResponse,
)
from app.services.tos_acceptance import (
    accept_tos, ClientIdentity, AcceptanceContext,
)
from app.services.tos_template import get_active_tos_template

# Adjust to DOC's existing auth dep (the operator-only auth, not the client one)
from app.api.deps import require_operator
# Adjust to DOC's existing email service
from app.services.email_service import send_signed_tos_to_both_parties

router = APIRouter(prefix="/api/tos", tags=["tos"])

SIGNED_PDF_DIR = Path("/data/tos_signed")  # under app_data Docker volume


@router.get("/template")
async def download_unsigned_template(
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the active unsigned TOS for the customer to read in-browser."""
    tpl = await get_active_tos_template(db)
    if tpl is None:
        raise HTTPException(404, "No TOS template configured")
    return Response(
        content=tpl.bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                'inline; filename="BarnardHQ-Terms-of-Service.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/accept",
    response_model=TosAcceptanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_terms(
    payload: TosAcceptanceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TosAcceptanceResponse:
    """Customer accepts. Fill the PDF, lock the fields, hash, persist, email."""
    tpl = await get_active_tos_template(db)
    if tpl is None:
        raise HTTPException(503, "No TOS template configured")

    # Honour X-Forwarded-For if behind Cloudflare Tunnel + reverse proxy
    fwd = request.headers.get("x-forwarded-for", "")
    client_ip = (fwd.split(",", 1)[0].strip()
                 if fwd else (request.client.host if request.client else "0.0.0.0"))

    client = ClientIdentity(
        full_name=payload.full_name,
        email=payload.email,
        company=payload.company,
        title=payload.title,
    )
    ctx = AcceptanceContext(
        ip=client_ip,
        user_agent=request.headers.get("user-agent", "")[:1000],
        accepted_at=datetime.now(timezone.utc),
    )

    # accept_tos is sync; FastAPI runs it in a threadpool when called from async.
    signed_bytes, record = accept_tos(
        client, ctx, template_bytes=tpl.bytes,
    )

    SIGNED_PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = SIGNED_PDF_DIR / f"{record.audit_id}.pdf"
    pdf_path.write_bytes(signed_bytes)

    row = TosAcceptance(
        audit_id=record.audit_id,
        customer_id=payload.customer_id,
        intake_token=payload.intake_token,
        client_name=record.client.full_name,
        client_email=record.client.email,
        client_company=record.client.company,
        client_title=record.client.title,
        client_ip=record.context.ip,
        user_agent=record.context.user_agent,
        accepted_at=record.context.accepted_at,
        template_version=tpl.version,
        template_sha256=record.template_sha256,
        signed_sha256=record.signed_sha256,
        signed_pdf_path=str(pdf_path),
        signed_pdf_size=len(signed_bytes),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Email both parties — non-blocking; log on failure but don't roll back the row.
    try:
        await send_signed_tos_to_both_parties(
            client_email=row.client_email,
            client_name=row.client_name,
            audit_id=row.audit_id,
            signed_pdf=signed_bytes,
        )
    except Exception as e:
        # log but do not fail the acceptance
        import logging
        logging.getLogger(__name__).exception(
            "TOS email failed for %s: %s", row.audit_id, e
        )

    return TosAcceptanceResponse(
        id=row.id,
        audit_id=row.audit_id,
        accepted_at=row.accepted_at,
        template_version=row.template_version,
        template_sha256=row.template_sha256,
        signed_sha256=row.signed_sha256,
        download_url=f"/api/tos/signed/{row.audit_id}",
    )


@router.get("/signed/{audit_id}")
async def download_signed_operator(
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    _user = Depends(require_operator),     # operator-only access
) -> FileResponse:
    """Operator pulls a signed copy by audit_id."""
    result = await db.execute(
        select(TosAcceptance).where(TosAcceptance.audit_id == audit_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Not found")
    return FileResponse(
        row.signed_pdf_path,
        media_type="application/pdf",
        filename=f"BarnardHQ-ToS-{row.audit_id}.pdf",
    )


@router.get("/signed/by-token/{intake_token}")
async def download_signed_by_token(
    intake_token: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Customer (still holding their intake token) pulls the signed copy.
    Token authenticates them; no operator login needed."""
    result = await db.execute(
        select(TosAcceptance).where(TosAcceptance.intake_token == intake_token)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Not found")
    return FileResponse(
        row.signed_pdf_path,
        media_type="application/pdf",
        filename=f"BarnardHQ-ToS-{row.audit_id}.pdf",
    )
```

### 2.7  Email helper

**File:** `backend/app/services/email_service.py` — add to the existing service:

```python
async def send_signed_tos_to_both_parties(
    *,
    client_email: str,
    client_name: str,
    audit_id: str,
    signed_pdf: bytes,
) -> None:
    """Send the signed TOS PDF to the client and BCC the operator."""
    from app.services.email_service import send_email  # existing send helper
    operator_email = await get_operator_email_from_settings()  # existing settings call

    subject = f"Signed Terms of Service — {audit_id}"
    body = (
        f"Hello {client_name},\n\n"
        "Attached is your signed copy of the BarnardHQ LLC Terms of Service. "
        f"Audit reference: {audit_id}\n\n"
        "Keep this for your records.\n\n"
        "BarnardHQ LLC\n"
    )
    await send_email(
        to=client_email,
        bcc=[operator_email] if operator_email else None,
        subject=subject,
        body=body,
        attachments=[(f"BarnardHQ-ToS-{audit_id}.pdf",
                      "application/pdf", signed_pdf)],
    )
```

If DOC's `send_email` signature differs, adapt to it without changing this function's name or arguments.

### 2.8  Register the router

**File:** `backend/app/main.py`

```python
from app.routes import tos  # NEW

app.include_router(tos.router)
```

### 2.9  Settings upload validation

**File:** `backend/app/routes/settings.py` — wherever the existing TOS upload endpoint lives, gate the upload behind the field-presence check:

```python
from app.services.tos_acceptance import template_has_required_fields

# inside the existing TOS upload endpoint, before persisting:
contents = await file.read()
if not template_has_required_fields(contents):
    raise HTTPException(
        status_code=400,
        detail=(
            "Uploaded PDF is missing required AcroForm fields. "
            "Required: client_name, client_email, client_company, client_title, "
            "signed_at_utc, client_ip, audit_id. "
            "Use the BarnardHQ ToS Rev 3 template."
        ),
    )
# … existing storage logic continues …
```

Bump the stored `tos_template_version` setting whenever a new PDF is uploaded.

---

## Phase 3 — Frontend

### 3.1  Acceptance page

**File:** `frontend/src/pages/TosAcceptance.tsx`

```tsx
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { acceptTos, getTemplatePdfUrl } from "../services/tosApi";

export default function TosAcceptance() {
  const [params] = useSearchParams();
  const intakeToken = params.get("token");
  const customerIdParam = params.get("customer_id");

  const [form, setForm] = useState({
    full_name: "", email: "", company: "", title: "",
  });
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ audit_id: string; download_url: string } | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!confirmed) {
      setError("Please confirm you have read and agree to the Terms.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await acceptTos({
        ...form, confirm: true,
        intake_token: intakeToken,
        customer_id: customerIdParam ? Number(customerIdParam) : null,
      });
      setDone({ audit_id: result.audit_id, download_url: result.download_url });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Acceptance failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="mx-auto max-w-3xl p-8 text-slate-100">
        <h1 className="font-bebas text-4xl text-cyan-400">ACCEPTED</h1>
        <p className="mt-4">
          Your acceptance is recorded. Audit ID:{" "}
          <code className="font-mono text-cyan-300">{done.audit_id}</code>
        </p>
        <p className="mt-2 text-sm text-slate-400">
          A copy has been emailed to you. You can also download it below.
        </p>
        <a
          href={done.download_url}
          className="mt-6 inline-block rounded bg-cyan-500 px-6 py-3 font-bold text-slate-900 hover:bg-cyan-400"
        >
          Download signed copy
        </a>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-8 text-slate-100">
      <h1 className="font-bebas text-4xl text-cyan-400">
        BARNARDHQ LLC — TERMS OF SERVICE
      </h1>
      <p className="mt-2 text-slate-300">
        Review the agreement below, fill in your information, and accept to proceed.
      </p>

      <iframe
        src={getTemplatePdfUrl()}
        title="Terms of Service"
        className="mt-6 h-[700px] w-full rounded border border-slate-700 bg-white"
      />

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="Full legal name" required value={form.full_name}
                 onChange={v => setForm({ ...form, full_name: v })} />
          <Field label="Email" type="email" required value={form.email}
                 onChange={v => setForm({ ...form, email: v })} />
          <Field label="Company / entity (optional)" value={form.company}
                 onChange={v => setForm({ ...form, company: v })} />
          <Field label="Title (optional)" value={form.title}
                 onChange={v => setForm({ ...form, title: v })} />
        </div>

        <label className="flex items-start gap-3 rounded border border-slate-700 bg-slate-900 p-4">
          <input
            type="checkbox" checked={confirmed}
            onChange={e => setConfirmed(e.target.checked)}
            className="mt-1 h-5 w-5 accent-cyan-500"
          />
          <span className="text-sm">
            I have read and agree to the BarnardHQ LLC Terms of Service. By checking
            this box and clicking <strong>Accept &amp; Sign</strong>, I am providing my
            electronic signature under the federal E-SIGN Act and Oregon&rsquo;s
            Uniform Electronic Transactions Act (ORS Ch. 84).
          </span>
        </label>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={submitting || !confirmed}
          className="rounded bg-cyan-500 px-8 py-3 font-bold text-slate-900 hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Signing…" : "Accept & Sign"}
        </button>
      </form>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", required = false }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-bold uppercase tracking-wider text-cyan-400">
        {label}{required && " *"}
      </span>
      <input
        type={type} value={value} required={required}
        onChange={e => onChange(e.target.value)}
        className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 focus:border-cyan-500 focus:outline-none"
      />
    </label>
  );
}
```

### 3.2  API client

**File:** `frontend/src/services/tosApi.ts`

```ts
import axios from "axios";

const api = axios.create();  // or use the existing axios instance

export interface TosAcceptancePayload {
  full_name: string;
  email: string;
  company: string;
  title: string;
  confirm: true;
  customer_id?: number | null;
  intake_token?: string | null;
}

export interface TosAcceptanceResult {
  id: string;
  audit_id: string;
  accepted_at: string;
  template_version: string;
  template_sha256: string;
  signed_sha256: string;
  download_url: string;
}

export const getTemplatePdfUrl = () => "/api/tos/template";

export async function acceptTos(payload: TosAcceptancePayload): Promise<TosAcceptanceResult> {
  const { data } = await api.post<TosAcceptanceResult>("/api/tos/accept", payload);
  return data;
}
```

### 3.3  Route registration

**File:** `frontend/src/App.tsx` (or wherever routes are registered)

```tsx
import TosAcceptance from "./pages/TosAcceptance";

// inside the public Routes:
<Route path="/tos/accept" element={<TosAcceptance />} />
```

The intake email DOC sends to customers should link to:
```
https://command.barnardhq.com/tos/accept?token={intake_token}&customer_id={customer_id}
```
Update the existing intake-email template if the URL pattern was different.

### 3.4  Wire intake into the new page

Phase 0 should have surfaced where DOC currently sends the customer for the TOS step. Wherever the intake link previously pointed at the canvas page, change it to `/tos/accept?token=…`. Search the email templates and the intake controller specifically.

---

## Phase 4 — Cleanup

After Phases 1–3 land and are tested, delete the canvas signature dead code from Phase 0's `grep` output. Common targets:

```bash
# From the repo root, on your dev machine
git rm frontend/src/components/SignatureCanvas.tsx           # adjust path
git rm frontend/src/components/SignaturePad.tsx              # adjust path
git rm backend/app/services/canvas_signature.py              # if exists
# Also remove imports + remaining references
grep -rn "SignatureCanvas\|SignaturePad" frontend/src        # confirm zero hits
grep -rn "tos_signature_data\|signature_png" backend/app     # confirm zero hits
```

If `react-signature-canvas` (or similar) is in `frontend/package.json` and used nowhere else:

```bash
cd frontend && npm uninstall react-signature-canvas signature_pad
```

---

## Phase 5 — Tests

**File:** `backend/tests/services/test_tos_acceptance.py`

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.services.tos_acceptance import (
    accept_tos, verify_signed_tos, template_has_required_fields,
    ClientIdentity, AcceptanceContext,
)

TEMPLATE = Path(__file__).parent / "fixtures" / "BarnardHQ-Terms-of-Service.pdf"


def test_template_has_required_fields():
    assert template_has_required_fields(TEMPLATE.read_bytes()) is True


def test_template_rejects_random_pdf():
    assert template_has_required_fields(b"%PDF-1.4\n...") is False


def test_round_trip():
    client = ClientIdentity("Test User", "test@example.com", "Co", "Ops")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="pytest",
                            accepted_at=datetime.now(timezone.utc))
    signed, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    assert verify_signed_tos(signed, record.signed_sha256) is True
    assert record.audit_id.startswith("DOC-")
    assert len(record.template_sha256) == 64
    assert len(record.signed_sha256)   == 64
    assert record.template_sha256 != record.signed_sha256


def test_email_normalization():
    client = ClientIdentity("X", "  MIXED@CASE.com  ", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="pytest",
                            accepted_at=datetime.now(timezone.utc))
    _, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    assert record.field_values["client_email"] == "mixed@case.com"


def test_missing_template_raises():
    client = ClientIdentity("X", "x@y.z", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="",
                            accepted_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="template"):
        accept_tos(client, ctx)


def test_locked_fields_after_fill():
    """Confirms /Ff bit 1 (ReadOnly) is set on every filled field."""
    from pypdf import PdfReader
    import io
    client = ClientIdentity("Test", "t@x.y", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="",
                            accepted_at=datetime.now(timezone.utc))
    signed, _ = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    r = PdfReader(io.BytesIO(signed))
    locked = 0
    for ann in r.pages[-1]["/Annots"]:
        obj = ann.get_object()
        if obj.get("/T"):
            assert int(obj.get("/Ff", 0)) & 1, f"{obj['/T']} not read-only"
            locked += 1
    assert locked == 7
```

Add a Phase 5 fixtures step: copy the Rev 3 PDF to `backend/tests/services/fixtures/BarnardHQ-Terms-of-Service.pdf` so the tests find it.

Add an httpx-based integration test that POSTs to `/api/tos/accept` against the test DB and verifies a row is created.

---

## Phase 6 — ADR

**File:** `docs/adr/0030-tos-acceptance-acroform.md`

```markdown
# ADR-0030: TOS Acceptance via PDF AcroForm Fill + SHA-256 Anchor

**Status:** Accepted
**Date:** 2026-XX-XX
**Supersedes:** the canvas-signature TOS pattern

## Context

DOC's previous TOS acceptance captured a finger- or mouse-drawn signature
on an HTML5 canvas, stored it as a base64 PNG separate from the static
TOS PDF. The signed artifact was two pieces — image + PDF — joined only
by a foreign key. Tampering with either could not be detected, and there
was no anchor proving which version of the TOS the customer accepted.

## Decision

Replace the canvas-signature flow with PDF AcroForm fill on a fillable
BarnardHQ ToS Rev 3 template. Seven named fields are filled and locked
read-only: client_name, client_email, client_company, client_title,
signed_at_utc, client_ip, audit_id. The pre-acceptance template bytes
are SHA-256 hashed (template_sha256) to anchor the version. The
post-fill PDF bytes are SHA-256 hashed (signed_sha256) for tamper
detection. Both hashes plus identity, IP, UA, timestamp, and audit_id
are persisted to a new tos_acceptances table. Settings page validates
that any uploaded TOS PDF has the seven required fields.

## Consequences

+ Single self-contained signed PDF — no detached signature image.
+ Tamper-evident via signed_sha256 round-trip.
+ Document-version anchored via template_sha256.
+ Old uploaded TOS PDFs without the required fields are rejected at
  the Settings upload endpoint.
+ Legally valid e-signature under federal E-SIGN Act and ORS Ch. 84
  (typed name + explicit checkbox + audit context = "executed by a
  person with the intent to sign").
- Operator must upload the Rev 3 fillable template (one-time).
- Historical canvas-signed acceptances remain in their original
  location and are not migrated.
- No drawn-signature visual on the printed page; visual evidence is
  the typed name in the locked AcroForm field.

## Alternatives considered

- Drawn signature + AcroForm fill, both captured (Option B): rejected
  for added complexity without legal benefit.
- Append a separate certificate page to the unmodified PDF: rejected;
  produces a longer document and re-introduces the two-piece-artifact
  problem.
- Third-party e-sign service (DocuSign, HelloSign): rejected; violates
  BarnardHQ's self-hosted, no-SaaS posture.
```

---

## Deployment

### On your Windows dev machine — branch + commit

```bash
cd C:\path\to\DroneOpsCommand
git checkout -b feat/tos-acroform-acceptance

# Drop in all new files per Phases 1–3
# Edit existing files per Phase 2.8, 2.9, 3.3, 3.4
# Delete dead code per Phase 4

git add -A
git commit -m "feat(tos): replace canvas signature with AcroForm fill+lock flow

- New tos_acceptance service module (pypdf-based fill+flatten)
- New tos_acceptances table with template/signed SHA-256 anchors
- New /api/tos/accept, /api/tos/template, /api/tos/signed/* routes
- New customer-facing TosAcceptance.tsx page (typed name + checkbox)
- Settings uploader validates required AcroForm field names
- Removed canvas SignaturePad widget (TOS-specific)
- Email signed copy to client; BCC operator
- ADR-0030"

git push origin feat/tos-acroform-acceptance
```

Open the PR. BOS-HQ self-hosted runner builds the image (per ADR-0029). Merge to `main` once green.

### On BOS-HQ — backup, pull, migrate, deploy

```bash
ssh bbarnard065@bos-hq

cd ~/droneops

# 1. Backup the DB before any migration
mkdir -p ~/backups
docker compose exec -T postgres pg_dump -U droneops droneops_db \
  > ~/backups/droneops-pre-tos-acroform-$(date +%Y%m%d-%H%M%S).sql

# 2. Pull merged main
git fetch origin
git status         # confirm clean working tree
git pull origin main

# 3. Pull the freshly built image
docker compose pull api

# 4. Run the Alembic migration
docker compose run --rm api alembic upgrade head

# 5. Bring the new code up
docker compose up -d --no-deps api frontend

# 6. Tail logs and confirm clean startup
docker compose logs -f --tail=100 api
```

### Upload the Rev 3 ToS PDF via the Settings UI

In DOC: **Settings → Customer Intake → Default TOS Document → Upload**.
Upload `BarnardHQ-Terms-of-Service.pdf` from your local machine. The new validation will reject any PDF lacking the seven AcroForm fields. Save.

### Smoke test

```bash
# From BOS-HQ — confirm template downloads
curl -sS -I https://command.barnardhq.com/api/tos/template
# Expect: 200 OK, Content-Type: application/pdf
```

```bash
# From your dev machine — open the public acceptance page
open https://command.barnardhq.com/tos/accept

# Fill the form, accept, confirm:
#   - 201 response with audit_id
#   - "Download signed copy" button works
#   - Downloaded PDF has all 7 fields filled and read-only
#   - Email arrives with attachment
```

```bash
# Verify the row landed
docker compose exec postgres psql -U droneops droneops_db -c "
SELECT audit_id, client_name, client_email, template_version,
       substring(template_sha256, 1, 12) AS tpl_sha,
       substring(signed_sha256,   1, 12) AS sig_sha,
       accepted_at
FROM tos_acceptances
ORDER BY accepted_at DESC LIMIT 5;"

# Verify the file exists
docker compose exec api ls -la /data/tos_signed/
```

---

## Rollback

If anything is broken in production:

```bash
ssh bbarnard065@bos-hq
cd ~/droneops

# Roll the migration back
docker compose run --rm api alembic downgrade -1

# Revert to previous image tag (BOS-HQ runner pushes :previous on each release)
git revert HEAD
docker compose pull api
docker compose up -d --no-deps api frontend
docker compose logs --tail=50 api
```

If row data is corrupted:

```bash
docker compose exec -T postgres psql -U droneops droneops_db \
  < ~/backups/droneops-pre-tos-acroform-YYYYMMDD-HHMMSS.sql
```

---

## Done.

When this lands:
- Customer goes to `/tos/accept?token=…`, reads the embedded PDF, types name + email + (optional) company/title, checks the box, clicks Accept.
- Backend fills the seven AcroForm fields, locks them ReadOnly, hashes both pre- and post-fill bytes, persists the row, writes the signed PDF to `app_data/tos_signed/`, emails both parties.
- Operator can re-download any signed copy by audit_id from `/api/tos/signed/{audit_id}`.
- Customer can re-download their copy via `/api/tos/signed/by-token/{intake_token}` for as long as their token is valid.
- Re-verification of any signed copy is a one-line SHA-256 round-trip against the stored `signed_sha256`.
