# ADR-0045: Phase 7 customer-surface hardening

**Date:** 2026-09-21
**Status:** Accepted — code + tests shipped to this worktree branch
(`security/phase7-customer-hardening`); **not deployed** (operator-gated,
Bill asleep at dispatch time). Governed by noc-master's
`docs/plans/2026-09-21-sso-fanout-dispatch.md` (Wave 2B) and
`docs/adr/0246-fleet-auth-posture-standard-and-the-ip-bypass-decision.md`
decision 5.

## Context

The 2026-09-18 fleet auth-posture audit (noc-master) flagged five concrete
defects on DroneOpsCommand's customer-facing surfaces. Per ADR-0246 decision
5, customer logins (EyesOn tenants, the DroneOps client portal and intake,
CS-Public users) stay app-local permanently — this ADR is **not** an SSO
migration. It closes the mechanisms underneath.

## Decision

### 1. Trusted-proxy-aware client IP resolution (`app/utils/client_ip.py`)

**The root cause underneath three of the five findings.** Every
`slowapi.Limiter` in this app (`main.py`, `auth.py`, `tos.py`,
`client_portal.py`, `intake.py`) used `key_func=get_remote_address`, which
reads `request.client.host` — the direct ASGI TCP peer. Because nginx
(`frontend`) always sits in front of uvicorn, that peer is **nginx's own
container IP on every single request**, never the caller's. Every "per-IP"
rate limit was therefore one shared global bucket, and `auth.py`'s login
lockout (`_check_lockout`/`_record_failure`) keyed on the same function — so
a stranger's failed logins could lock the operator out of their own
instance.

Three per-router `_client_ip()` helpers (`tos.py`, `client_portal.py`,
`intake.py`) already existed for *logging*, but trusted the **leftmost**
`X-Forwarded-For` hop unconditionally. nginx's
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
(`frontend/nginx.conf`) *appends* to whatever the client sent, so a client
setting its own `X-Forwarded-For: 1.2.3.4` gets relayed as
`X-Forwarded-For: 1.2.3.4, <real address>` — trusting the leftmost hop lets
any caller pick its own logged/rate-limited identity. This was a real,
separate spoofing bug in the audit-trail IP fields (relevant here because
this repo stores executed TOS documents and invoice records).

**Fix:** one shared, tested function, `get_trusted_client_ip(request)`,
used everywhere the old `get_remote_address`/`_client_ip()` calls were:

- Trusts `X-Forwarded-For` **only** when the direct TCP peer is a known
  trusted proxy. Trust sources: (a) **dynamic** — `TRUSTED_PROXY_HOSTNAME`
  (default `"frontend"`) resolved via Docker's embedded DNS at call time,
  cached 30s — mirrors `frontend/nginx.conf`'s own
  `resolver 127.0.0.11 valid=10s` pattern, so a container recreate
  (redeploy) is handled automatically with **no compose network topology
  change and no operator action**; (b) **static** — optional
  `FORWARDED_ALLOW_IPS` (comma-separated IPs/CIDRs) for non-default
  topologies. Loopback is always trusted (matches uvicorn's own default).
- When trusted, takes the **rightmost non-trusted hop** — the one nginx
  itself appended — never the leftmost. Mirrors uvicorn's own
  `ProxyHeadersMiddleware` algorithm exactly (verified against the
  installed `uvicorn==0.34.0` source).
- Fails **closed**: an unresolvable/misconfigured trust source means
  `X-Forwarded-For` is ignored entirely and the raw peer is returned —
  never fail open into trusting a client-supplied header.

Tests: `backend/tests/test_client_ip.py` (12 cases — the two load-bearing
ones are `test_spoofed_header_from_untrusted_peer_is_ignored` and
`test_trusted_peer_client_injected_leftmost_hop_is_not_trusted`, proving
spoofing fails from both an untrusted peer AND a client-injected leftmost
hop through a *genuinely* trusted proxy).
`backend/tests/test_auth_lockout_per_client.py` proves the headline
consequence end-to-end: a stranger failing login 5x does not lock out a
different real client, and a successful login from one client does not
reset another's lockout clock.

### 2. `POST /api/tos/accept` unauthenticated write (`tos.py`)

Pre-fix, this route trusted `payload.customer_id` outright — the
`intake_token` field existed only as an opaque correlation string, never
validated. Any caller who knew or enumerated a customer UUID could POST
arbitrary `full_name`/`email`/`company` and the handler would (a) write a
signed TOS acceptance under that customer_id, and (b) a few lines later,
overwrite that customer's stub name/email and flip `tos_signed=True` —
using attacker-supplied identity data, against a real customer record, with
zero proof of authorization.

**Fix:** mirrors `intake.py`'s own established pattern
(`get_intake_form`/`submit_intake_form` already derive the customer FROM
the token, never trust a client-supplied id). `customer_id` is now
*resolved from* a validated, unexpired `intake_token`:

- `intake_token` present → look up `Customer.intake_token`; 404 if no
  match, 410 if `intake_token_expires_at` has passed, 404 if the payload's
  `customer_id` doesn't match what the token resolves to (a
  forged/copy-pasted-link shape).
- `customer_id` present with **no** token → 400. The frontend
  (`TosAcceptance.tsx`) never sends this combination — both come from the
  same intake URL — so a request shaped this way is exactly the forgery
  vector being closed.
- Neither present → unchanged, the documented anonymous "cold visitor"
  path (`TosAcceptanceRequest`'s own docstring). No existing customer
  record is touched.

Tests: `backend/tests/test_tos_accept_forgery_prevention.py` (unauthorized
customer_id, unknown token, expired token, token/customer_id mismatch, cold
visitor still works, full-ASGI 400 check).
`backend/tests/test_tos_customer_sync.py` and
`test_tos_accept_route_body.py` — pre-existing suites whose fixtures
exercised exactly the insecure shape (`customer_id` with no token) — were
updated to supply a matching, unexpired token, per this repo's "fix
propagated defects when encountered" standard rather than leaving them
green against the old contract.

### 3. Signed-TOS download link had no expiry and no rate limit (`tos.py`)

`GET /api/tos/signed/by-token/{intake_token}` had **no** `@limiter.limit`
decorator (every other public route in this file has one) and explicitly
never gated on `intake_token_expires_at` — by design, so the customer keeps
durable access to their own signed copy after the shorter intake window
closes. But "durable" meant literally unbounded: a token that ever leaked
(shared inbox, proxy log, forwarded email) remained a valid,
unlimited-rate, unrevocable PII-download credential (name, email, company,
address on the signed PDF) forever — "rotating" the customer's intake token
does not touch this row's independently-captured `intake_token` value.

**Fix, deliberately conservative** (this repo's "don't remove working
functionality without presenting the trade-off" standard, and the durable-
access design is real and intentional): bounded rather than removed.
`settings.tos_signed_download_expire_days` (default 730 — ~2 years,
generous) computed from `TosAcceptance.accepted_at`; `@limiter.limit
("10/minute")` added, matching this file's existing cadence. Not tied to
`customers.intake_token` — that would silently break durable access on a
routine token rotation for unrelated business, a bigger behavior change
this pass doesn't make unilaterally.

Tests: `backend/tests/test_tos_signed_download_expiry.py`.

### 4. Revoked client links stayed valid until JWT expiry (`auth/client_auth.py`)

`DELETE /api/missions/{id}/client-link/{token_id}` (`client_portal.py:1047`
per the audit's line reference) stamps `ClientAccessToken.revoked_at` — but
`get_current_client` (the actual auth dependency gating every
`/api/client/*` endpoint) never queried that table. It verified the JWT's
signature and `exp` and nothing else. A "revoked" link stayed fully
functional for up to `settings.client_token_expire_days` (default 30) after
the operator revoked it.

The fix required no new plumbing: `token_hash` was **already** populated on
every issuance path (`client_portal.py`'s `_get_or_create_client_link`,
`missions.py`) via the existing `hash_token()` helper — it was simply never
read at authentication time. `get_current_client` now looks up
`ClientAccessToken` by `token_hash`, and rejects (401) if the row is
missing, `revoked_at` is set, or `expires_at` has passed (checked
independently of the JWT's own `exp` — the DB row is the operator-
controlled, revocable source of truth). A successful check also bumps
`last_accessed_at` for observability.

Tests: `backend/tests/test_client_portal_revocation.py` — revoked token
rejected, active token accepted (and bumps `last_accessed_at`), no matching
row rejected, DB-expired-but-JWT-still-valid rejected, and revocation is
scoped to the specific token (a second live token for the same customer
keeps working).

**Blast-radius finding while auditing this fix:** `client_login`
(`POST /api/client/auth/login`, the password-based repeat-customer login)
mints a fresh JWT via `create_client_token(...)` that aggregates
mission_ids across the customer's existing active `ClientAccessToken`
rows — but never recorded a row for that NEW aggregated token itself. This
was invisible before because `get_current_client` never queried the table
at all; with the fix above, that freshly-issued token would 401 on its
very first use — a real functional regression this pass would otherwise
have shipped. `client_login` now creates the matching row, mirroring
`_get_or_create_client_link` / `_send_portal_email_for_mission`'s existing
shape. Regression test:
`backend/tests/test_client_login_issues_valid_token.py` — a full round
trip through `client_login` then `get_current_client` with the exact token
returned, using a fake session that captures what `add()` recorded rather
than a fixture pre-supplying the "right" row.

### 5. `cs-api.barnardhq.com/api/archive/search` answers unauthenticated (CS-Public)

**Different repo — `~/repos/CallSignPublic`, confirmed via `git remote -v`
(origin `github.com/BigBill1418/CallSignPublic.git`, reachable), NOT
`~/callsign`.** Per the dispatching agent's instruction not to create a
worktree there myself, this is delivered as a reviewed patch
(`docs/patches/0075-cspublic-search-origin-auth.patch` in that repo, staged
here at hand-off) rather than committed. See that patch's own commit
message / this repo's CHANGELOG entry for the mechanism: a Worker->origin
constant-time Bearer (`ORIGIN_SEARCH_SECRET`), mirroring CallSignPublic's
own established `ingest.py` pattern, added to `handleSearch` in
`worker/api/src/index.ts` and checked by a new `_require_worker_bearer` in
`backend/app/api/archive.py` — application-layer only, no Cloudflare Access
or Worker-routing change (out of scope per Wave 2B's constraint that
another agent holds the Cloudflare Access API).

## Consequences

- **No live behavior changes on deploy without operator action for #5.**
  The CS-Public patch is fail-closed by design (mirroring `ingest.py`): if
  merged and deployed without ALSO setting `search.worker_origin_token` in
  the origin's Settings UI and `ORIGIN_SEARCH_SECRET` via `wrangler secret
  put` on the Worker, **every archive search — including through the
  legitimate `cs.barnardhq.com` path — 503s.** This patch is NOT
  self-contained-safe to merge-and-deploy blind; the two secrets must be
  provisioned first. Flagged prominently in the patch hand-off.
- Items 1–4 (DroneOpsCommand) are deploy-safe with no operator
  pre-provisioning — the dynamic proxy-trust resolution needs no config,
  and the new settings (`FORWARDED_ALLOW_IPS`, `TRUSTED_PROXY_HOSTNAME`,
  `TOS_SIGNED_DOWNLOAD_EXPIRE_DAYS`) all default to safe, working values.
  Verified via `docker compose config` against both
  `docker-compose.bos-prod.yml` and `docker-compose.demo.yml` — the new
  `backend.environment` entries merge through correctly, no override file
  changes needed.
- A leaked signed-TOS download token is no longer permanently exploitable
  — bounded to 730 days, operator-tunable via `TOS_SIGNED_DOWNLOAD_EXPIRE_DAYS`.
- `intake.py`'s `/form/{token}` PII surface (audit item 7.2) already carried
  a `30/minute` `@limiter.limit` — the audit's "no rate limit" was accurate
  in effect (defect #1 made it a shared/global bucket, not a per-caller
  one) but the decorator itself was already present; item 1's fix closes
  the practical gap. **CAPTCHA remains unimplemented and is an explicit
  open recommendation**, not silently dropped: the 256-bit
  `secrets.token_urlsafe(32)` intake token makes brute-force guessing
  infeasible regardless, so the marginal benefit of a CAPTCHA (which would
  need a new Cloudflare Turnstile site key — an operator/Cloudflare-
  dashboard action out of scope for this pass) is lower than for a
  guessable-ID surface. Recorded here rather than silently closed.

## Rollout

DroneOpsCommand (items 1–4): standard `git push` → NOC fleet-deployer path
(`ADR-0018`) once merged to `main` by the operator. No manual steps. Verify
post-deploy: `curl -s https://droneops.barnardhq.com/openapi.json | jq -r
.info.version` reads `2.91.0`.

CS-Public (item 5): **do not merge/deploy until both secrets are set** —
see the patch hand-off note and `docs/patches/0075-cspublic-search-origin-auth.patch`.

## Rollback

DroneOpsCommand: revert the merge commit; all five DB columns/tables
touched (`ClientAccessToken`, `Customer`, `TosAcceptance`) are pre-existing
— no migration was added or needs reversing. The `.env`-driven settings
default to their pre-fix-equivalent safe state if the revert lands (no
env cleanup required).

CS-Public: revert the Worker deploy first (or clear
`ORIGIN_SEARCH_SECRET`), THEN the origin deploy — reverse order relative to
rollout, so the Worker never sends a header an already-reverted origin
doesn't expect (harmless either order in practice, since an unrecognized
header is simply ignored by both old and new code, but stated for
completeness).
