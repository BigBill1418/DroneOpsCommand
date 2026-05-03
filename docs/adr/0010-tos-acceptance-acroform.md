# ADR-0010: TOS Acceptance via PDF AcroForm Fill + SHA-256 Anchor

**Status:** Accepted
**Date:** 2026-05-03
**Supersedes (in part):** the canvas-signature TOS pattern in
`backend/app/routers/intake.py` + `frontend/src/pages/CustomerIntake.tsx`
for *new* TOS acceptances. Historical canvas-signed records remain in
place; see Consequences below.
**Related:**
- `docs/TOS-Rebuild.md` (the canonical 1045-line spec)
- `docs/superpowers/specs/2026-05-03-deposit-and-tos-rebuild-design.md`
  §4 (the per-repo deltas applied)
- `docs/adr/0008-customer-payment-gated-on-mission-completion.md`
  (extended by the parallel deposit feature, ADR-0009)

## Context

Until today, the customer's acceptance of the BarnardHQ Terms of
Service was captured as a finger- or mouse-drawn HTML5 canvas
signature stored as a base64 PNG in `customers.signature_data`,
separate from the static PDF that lives at
`${UPLOAD_DIR}/tos/default_tos.pdf`. The acceptance was therefore
**two artifacts loosely joined by a customer foreign key**:

1. The unsigned PDF on disk (re-uploadable, replaceable, no version
   anchor stored anywhere).
2. The PNG signature in the DB (no link to the *bytes* of the PDF
   that was on disk at the moment of signing).

Three problems followed:

* **Untraceable document version.** A re-uploaded TOS overwrote
  `default_tos.pdf` and there was no record on the customer's row
  of *which version* of the TOS they had agreed to.
* **No tamper detection.** Either artifact could be edited
  (file-system or DB) and nothing would catch it.
* **Detached visual.** When the operator pulled the "signed TOS"
  via `/api/intake/{customer_id}/signed-tos`, the signature image
  was composited onto the PDF at request time using ReportLab —
  a visual artifact, not the artifact the customer signed.

We now have the BarnardHQ Rev 3 TOS PDF (uploaded 2026-05-02 to
`/data/uploads/tos/default_tos.pdf`, 158826 bytes, all 7 AcroForm
fields present on page 7) and the deposit feature (ADR-0009)
about to ship — paying customers must sign a TOS that we can later
prove they signed, against a fixed document version, in a tamper-
evident way.

## Decision

Replace the canvas-signature flow for *new* acceptances with a
**typed-name + checkbox + AcroForm fill** flow against the Rev 3
template. Seven named fields are filled and locked
(`/Ff` bit 1 = ReadOnly):

```
client_name      client_email   client_company    client_title
signed_at_utc    client_ip      audit_id
```

Two SHA-256 hashes anchor the artifact:

* `template_sha256` — over the unsigned template bytes, **before**
  fill. Pins the document version the customer agreed to.
* `signed_sha256` — over the post-fill bytes. Tamper-detection
  round-trip: `hashlib.sha256(stored_pdf).hexdigest() ==
  row.signed_sha256`.

Both hashes plus identity, IP, user-agent, timestamp, audit_id, and
the on-disk path of the signed PDF land in a new `tos_acceptances`
table. Subsequent verification is a one-line hash compare against
the stored `signed_sha256`.

A new public route at `POST /api/tos/accept` performs the fill +
lock + persist + email cycle and returns `{audit_id, template_sha256,
signed_sha256, download_url, …}`. Public `GET /api/tos/template`
serves the active unsigned template for the in-page iframe; public
`GET /api/tos/signed/by-token/{intake_token}` lets the customer
re-download their own signed copy; operator-only
`GET /api/tos/signed/{audit_id}` lets the operator pull any signed
copy.

Settings upload of the TOS PDF (both the per-customer
`POST /api/intake/{id}/upload-tos` and the default
`POST /api/intake/upload-default-tos`) is gated by
`template_has_required_fields()` — any PDF missing any of the seven
named fields is rejected at upload time so the operator never
silently configures a TOS that cannot be filled.

## Per-repo deltas applied (vs. `docs/TOS-Rebuild.md`)

The base spec was written against a hypothetical Alembic-managed
schema and a different hostname. This repo has neither. The
following adjustments are intentional:

| Spec said | This repo | Why |
|---|---|---|
| ADR-0030 | **ADR-0010** | Repo's actual ADR sequence — current head is 0008 (today), 0009 is the parallel deposit feature, this is 0010. |
| `command.barnardhq.com` | **`droneops.barnardhq.com`** | Actual production hostname per design doc §4. |
| Alembic migration `0030_tos_acceptances.py` | **`Base.metadata.create_all` + new model** | This repo does not use Alembic; the additive-migration pattern in `backend/app/main.py:_add_missing_columns` runs alongside `create_all`, which creates the new `tos_acceptances` table on the next container boot. No migration file is required because the table is brand new (no existing-table `ALTER` to manage). |
| `customers.id` is `Integer` | `customers.id` is **UUID** | The model's `customer_id` FK is `UUID(as_uuid=True)` to match. |
| `backend/app/routes/tos.py` | **`backend/app/routers/tos.py`** | Repo convention is `routers/`, not `routes/`. |
| `frontend/src/services/tosApi.ts` | **`frontend/src/api/tosApi.ts`** | Repo convention is `api/`. |
| `frontend/src/pages/TosAcceptance.tsx` uses Tailwind | **Uses Mantine** | Tailwind is not installed; Mantine is the project's UI library. |
| `intake_url = f"{frontend_url}/intake/{token}"` | **`f"{frontend_url}/tos/accept?token={token}&customer_id={customer.id}"`** | Customer lands on the new TOS page first per design doc §4. |

## Consequences

### Positive

- **Single self-contained signed artifact.** No detached signature
  PNG; the typed name lives inside the locked PDF the customer
  signed.
- **Tamper-evident.** `signed_sha256` round-trip catches any
  byte-level edit.
- **Document-version anchored.** Future operators can produce the
  exact unsigned bytes a customer agreed to by hash.
- **Field-presence gated upload.** A bad TOS PDF is rejected at
  upload time, not at customer-acceptance time.
- **Legally valid e-signature** under the federal E-SIGN Act and
  Oregon ORS Ch. 84 (typed name + explicit checkbox + audit
  context = "executed with intent to sign").
- **Best-effort email is best-effort.** Email failure does not roll
  back the audit row or signed-PDF write. The operator can re-send
  via the existing operator-side download endpoint.

### Negative / accepted

- **Phase 4 cleanup is a no-op.** The legacy
  `frontend/src/pages/CustomerIntake.tsx` canvas-signature widget
  remains in place because it backs the still-live
  `/intake/{token}` flow that the wider intake form depends on.
  Per `docs/TOS-Rebuild.md` §1.2 we explicitly leave the old
  acceptance path and historical canvas-signed records alone — they
  are read-only history; new acceptances flow through the new path.
  A future ADR can deprecate `CustomerIntake.tsx` once the operator
  has switched all live intake links to the new URL.
- **Two TOS surfaces transiently coexist.** The existing
  `/api/intake/upload-default-tos` UI still uploads the same file
  the new `/api/tos/template` reads from. The new field-presence
  gate prevents drift; the storage location is shared on purpose.
- **Customer intake data not migrated.** Existing `tos_signed`,
  `tos_signed_at`, `signature_data` columns on `customers` remain
  untouched; old canvas-signed acceptances retain their original
  shape (Phase 1.2 of `TOS-Rebuild.md` mandates this — they have no
  AcroForm-field analog and forcing them into the new schema would
  lie about their integrity).
- **No drawn-signature visual on the printed page.** Visual evidence
  is the typed name in the locked AcroForm field. This is the
  industry-standard pattern for AcroForm e-signing (it is what
  DocuSign's fallback-typed-name flow produces) but operator-readable
  customers may need a one-line explainer.

### Out-of-scope follow-ups

- Cloudflare Access bypass to expose `/tos/*` and `/api/tos/*`
  publicly through the existing customer-portal CF Access app.
  Owned by the orchestrator; will land after merge.
- Operator-side TOS-acceptance review UI (browse the
  `tos_acceptances` table). For now: `psql` or the operator-only
  download endpoint.
- Deprecate `CustomerIntake.tsx` canvas widget once intake email
  cutover is complete and no operator is sending the old URL.

## Alternatives considered

- **Drawn-signature + AcroForm fill, both captured (Option B).**
  Rejected for adding complexity with no legal benefit (drawn
  signature is not what the E-SIGN Act requires).
- **Append a separate certificate page to the unmodified PDF.**
  Rejected — produces a longer document and re-introduces the
  two-piece-artifact problem.
- **Third-party e-sign service (DocuSign, HelloSign).** Rejected;
  violates BarnardHQ's self-hosted, no-SaaS posture and adds a
  per-acceptance fee.

## Verification

- Hermetic helper tests:
  `backend/tests/services/test_tos_acceptance.py` — 12/12 pass
  against the actual prod PDF fixture pulled from BOS-HQ.
- TypeScript strict mode passes (`npx tsc --noEmit`).
- Vite production build clean (`TosAcceptance` chunk 4.87 KB
  gzipped 1.97 KB).
- Live verification deferred to operator post-merge once the orchestrator extends Cloudflare Access.
