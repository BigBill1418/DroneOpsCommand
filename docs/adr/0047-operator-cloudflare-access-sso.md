# ADR-0047: Operator Cloudflare Access SSO — verify the JWT, then retire the password

- **Status:** Accepted — Step A (verification) and Step B (local-login kill switch) both
  implemented on `feat/operator-sso`; **neither deployed**. Step B is off by default
  everywhere and requires a separate operator action to enable.
- **Date:** 2026-09-21
- **Under:** noc-master `docs/adr/0246-fleet-auth-posture-standard-and-the-ip-bypass-decision.md`
  decisions 1, 2, and 5. Implements `docs/plans/2026-09-18-fleet-sso-conversion-roadmap.md`
  Phase 4.4.
- **Reference implementation copied from:** `~/marketing/api/cf-access.js` +
  `~/marketing/api/auth.js` (marketing ADR-0099, noc-master ADR-0246 roadmap Phase 2
  pilot) — same fail-closed contract and identity-mapping pattern, ported from Node/`jose`
  to Python/`python-jose` + `httpx`.
- **Also read:** `noc-master/docs/plans/2026-09-21-marketing-droneops-interaction-map.md`
  §2 (the DroneOpsCommand-specific findings that bound this change's scope) and
  `noc-master/docs/adr/0247-noc-control-plane-phase-1-containment.md` (unrelated stack,
  cited here only for its methodological lesson: a config that *renders* correctly, or a
  fix *verified against the wrong vantage*, is not the same as a fix verified against the
  live system — carried into both the JWKS fail-closed contract and this ADR's insistence
  on a two-step, independently-verifiable cutover).

## Context

`droneops.barnardhq.com/` already returns a Cloudflare Access 302 — but that is edge-only.
Nothing in `backend/` ever verified the Access JWT; the app trusts the perimeter and
authenticates operators with its own bcrypt password + HS256 bearer JWT
(`backend/app/auth/jwt.py`, `backend/app/routers/auth.py`). Per noc-master ADR-0246
decision 2, an app may only have its local login removed once it verifies the Access JWT
itself — removing local auth from an app that merely trusts the perimeter turns a
password-protected origin into an *unauthenticated* one for every container reachable over
the WireGuard mesh (BOS-HQ, where this stack runs).

**Scope, precisely.** ADR-0246 decision 5 keeps customer-facing authentication app-local
*permanently*: the client portal (`frontend/src/pages/client/ClientLogin.tsx`), `/api/client/*`,
and `/intake/*`/`/tos/*` users are not in BarnardHQ's IdP and never will be. This ADR touches
**only** the operator login — the single `get_current_user` dependency
(`backend/app/auth/jwt.py`) that all 25 operator routers depend on. It is verified separately
from, and never imported by, `backend/app/auth/client_auth.py` (the customer JWT path).

**DroneOpsCommand is not BarnardHQ-exclusive software.** It is MIT-licensed
(`LICENSE`) and explicitly self-hostable (`README.md`: "100% self-hosted... runs on your own
hardware"), with a public demo instance (`command-demo.barnardhq.com`,
`docker-compose.demo.yml`) that ships local `demo`/`demo123` credentials and has no
Cloudflare Access in front of it at all. Every change here had to remain **fully inert** for
both of those deployments by default — this is the central design constraint that
distinguishes this ADR from the marketing pilot it borrows from, which has no third-party
distribution to protect.

## Decision

### Step A — verify the Access JWT, additively

1. **`backend/app/auth/cf_access.py`** — RS256 verification against
   `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, checking `iss`, `aud`, `exp`
   (30s clock skew), and an `email` claim against an allow-list (default
   `bill@barnardhq.com`, ADR-0055's canonical identity). Built on `python-jose` (already a
   pinned dependency, `==3.4.0`, CVE-2024-33663/33664-fixed) — the asymmetric crypto is
   never hand-rolled, and an explicit `algorithms=["RS256"]` allow-list closes the
   alg-confusion class before any key is tried (`jose.jws._verify_signature` checks
   `alg in algorithms` first, so `alg: none` / `alg: HS256` is rejected outright).
   `python-jose` has no built-in caching remote-JWKS helper (unlike Node's `jose`), so this
   module implements its own: 1-hour TTL, 30-second fetch-attempt cooldown, one throttled
   forced refetch-and-retry on a verification failure that might be a key rotation.
   **Fail-closed on every path, deliberately narrower than TitanForge's stale-if-error JWKS
   cache**: once a cached keyset's TTL has elapsed, a fetch failure denies — it is never
   served past its TTL. 33 tests (`backend/tests/test_cf_access.py`) cover the happy path and
   every documented failure mode (wrong `iss`/`aud`, expired, forged signature, missing/absent
   `email`, disallowed email, unreachable JWKS, malformed JWKS response, `alg` downgrade,
   TTL/cooldown/rotation behavior) using a real generated RSA keypair — no mocked crypto.
2. **Identity mapping, not adoption** — `cf_access_identities` (email -> `users.id`,
   `backend/app/models/cf_access_identity.py`, migration `0012_cf_access_ident`). Populated
   *only* by `resolve_cf_access_user()` in `backend/app/auth/jwt.py`. This is the direct fix
   for the exact defect a security review caught in the marketing pilot's first draft
   (marketing ADR-0099 correction, 2026-09-21): a lookup keyed on `users.username` can
   silently adopt a pre-existing local account. DroneOpsCommand has **no `role` column on
   `users` at all** (verified — zero `ForeignKey("users.id")` references anywhere else in
   `app/models/`, confirming this is a single-operator tool with no per-user data
   partitioning), so the "auto-granted admin" half of that incident doesn't apply here — but
   the adoption risk is *more* acute in this codebase than in marketing's: this app places
   **no charset restriction** on `PUT /api/auth/account`'s username field (marketing's
   equivalent route enforces `/^[a-zA-Z0-9_.-]+$/`), so an operator-renamed local account
   really could collide with the shadow-provisioning naming convention
   (`cf-access:<email>`). `resolve_cf_access_user()` therefore never falls back to adopting a
   name-colliding row on that race — it denies with 503 and logs the collision. Proven against
   a real disposable Postgres container (`backend/tests/test_cf_access_identity_resolution.py`,
   6 tests, opt-in via `DOC_TEST_PG_URL` — a mocked session can't reproduce real
   UNIQUE-constraint semantics), including the exact adoption scenario: a pre-existing local
   row squatting the shadow username is left untouched and unmapped.
3. **Wired into `get_current_user` as an additional accepted credential, not a
   replacement** — same shape as the marketing pilot's Express `authMiddleware`. A CF-Access
   header that fails verification (or is absent) falls through to the existing bearer-token
   check rather than 401ing immediately; only a *successful* verification short-circuits
   straight past it. `HTTPBearer(auto_error=False)` replaces the default `HTTPBearer()` so a
   pure-Access request (no `Authorization` header at all — the real shape of a browser
   request once Access fronts the hostname) doesn't 403 before `get_current_user`'s body
   runs. 7 tests (`backend/tests/test_get_current_user_cf_access.py`) pin the decision logic:
   unconfigured is byte-identical to pre-ADR-0047 (even a present-but-ignored Access header
   never triggers a verifier call), configured-but-failing falls through, configured-and-valid
   authenticates with zero bearer token, and Access wins when both credentials would
   independently succeed.
4. **Structurally dark by default** — verification requires **both**
   `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` non-empty (`backend/app/config.py`); both are
   blank by default in `docker-compose.yml`/`.env.example`. Until an operator sets both,
   `is_cf_access_configured()` reads `False`, the CF-Access branch of `get_current_user`
   never runs, and `httpx` never makes a network call — a no-op for self-hosted/OSS installs
   and the public demo instance, by construction, not by a separate flag that could be
   flipped on accidentally.

### Step B — the kill switch (implemented, OFF by default, operator-gated to enable)

`LOCAL_LOGIN_DISABLED` (`backend/app/config.py`, default `false`). When true:

- `POST /api/auth/login`, `POST /api/auth/setup`, `PUT /api/auth/account`, and
  `POST /api/auth/refresh` all return `403` with a message pointing at Access, and mint no
  tokens.
- `GET /api/auth/setup-status` reports `needs_setup: false` unconditionally (same shape as
  the existing `managed_instance` short-circuit) — no reason to run the setup wizard for a
  local account that can never log in.
- `get_current_user`'s **bearer-token verification logic is left completely intact** — only
  the routes that *mint* new local credentials are gated, not the code that validates one. A
  pre-existing, still-unexpired local JWT stops working only because `/refresh` is gated
  (no new tokens after expiry); this is a deliberate, config-reversible boundary, not a
  code deletion, so re-enabling local login is a single env-var flip with no redeploy of
  removed code.

**Why implemented-but-off, not deferred like the marketing pilot's equivalent (roadmap 2.4,
explicitly not done in ADR-0099):** this task's brief asked for both steps landed as separate,
reviewable commits specifically so Step B "can be held back." The **default value** is the
actual safety mechanism — `false` everywhere, including `docker-compose.bos-prod.yml`
(untouched by this change) — not a decision to defer the code. Flipping it is a single
operator action, taken only after Step A has soaked in production; see the cutover runbook
below.

**Never applies to the client portal.** `LOCAL_LOGIN_DISABLED` is read nowhere in
`backend/app/auth/client_auth.py`, `frontend/src/pages/client/ClientLogin.tsx`, or the
intake/TOS routes — those stay app-local regardless of this flag's value, per ADR-0246
decision 5.

## Part 2 — the login screen

`frontend/src/pages/Login.tsx` was a password form. It now:

1. **Fixes the operator email** (`Login.tsx`, `Setup.tsx`) from `me@barnardHQ.com` to
   `Bill@BarnardHQ.com` — exact capitalization, both the `mailto:` and the visible text.
2. **Attempts silent SSO first.** `useAuth`'s init sequence now reads `sso_configured` off
   the (already-fetched, public) `/api/auth/setup-status` response and, when true, probes
   `GET /api/auth/account` with **no bearer token** via a bare `axios` call (bypassing the
   shared API client's refresh/redirect interceptor deliberately — a probe failure here must
   fall through to the ordinary login screen, never force-navigate). Cloudflare Access
   injects the `Cf-Access-Jwt-Assertion` header on every proxied request automatically once
   an Access app exists for the hostname, so a real operator browsing to
   `droneops.barnardhq.com` never types a password: the probe succeeds before the login form
   ever renders. This is fully inert (no extra network round trip at all) when
   `sso_configured` is false — the self-hosted/demo case.
3. **The footer now matches CallSignLane's pattern exactly**
   (`~/repos/CallSignPublic/frontend/src/App.tsx:1437-1457`): an anchor to
   `https://www.barnardhq.com` wrapping the label **"A software solution by:"** (operator's
   exact wording — CallSignLane says "Project by") plus the real BarnardHQ wordmark SVG
   (`frontend/public/barnardhq-logo.svg`, copied byte-for-byte from CallSignPublic — Sora-700
   letterforms baked to paths, zero runtime font loads, matching barnardhq.com's `.nav-logo`).
   No placeholder or regenerated wordmark.
4. When `local_login_disabled` is true, the password form is hidden entirely (SSO-only
   messaging); when false (the default, and the only state self-hosted/demo ever see), the
   password form remains exactly as before, just visually restyled to fit the same dark
   Mantine / Share Tech Mono / `#00d4ff` language the rest of the app already uses.

## Cutover runbook (for the operator — not executed by this change)

**Precondition, already true and untouched by this change:** the Access application for
`droneops.barnardhq.com` already exists and is correctly configured (per the brief — this
change makes zero Cloudflare API calls and creates/modifies nothing at the edge).

1. **Enable Step A.** On BOS-HQ, set `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` (read the
   AUD tag from the Cloudflare Zero Trust dashboard -> Access -> Applications -> the
   `droneops.barnardhq.com` app -> Overview) in the host's env, redeploy. Confirm the
   startup log line `[CF-ACCESS] SSO verification ENABLED — team=... allowlist=1 email(s)`.
   Password login continues to work throughout — this step is purely additive.
2. **Soak.** Browse to `droneops.barnardhq.com` as the operator; confirm the login screen is
   skipped entirely (silent SSO) and `docker logs droneops-backend-1` shows no
   `[CF-ACCESS]` warnings on that session. Let this run for a real operating period (days,
   not minutes) before touching Step B — this is the soak the brief and ADR-0246 decision 2
   both require.
3. **Enable Step B.** Only after step 2 has held: set `LOCAL_LOGIN_DISABLED=true`, redeploy.
   `POST /api/auth/login` now 403s; the operator's only path in is Access.

## Blast radius

- **Existing operator account(s):** untouched by Step A (additive). Once Step B is enabled,
  the local account's password can no longer be used to log in, but the row itself, and any
  data referencing it, is untouched — there is no foreign key from any business table to
  `users.id` in this schema (verified), so nothing else changes shape.
- **`/api/auth/login` and the setup wizard once Step B lands:** `login`/`setup`/`account`/
  `refresh` all 403; `setup-status` always reports `needs_setup: false`. Fully reversible by
  flipping `LOCAL_LOGIN_DISABLED` back to `false` — no code was deleted.
- **Self-hosted/OSS installs and the public demo instance:** zero change in either step,
  by construction (both env vars empty/false is their permanent default state).
- **Managed-tenant instances** (`docs/managed-hosting.md`, `MANAGED_INSTANCE=true`): also
  unaffected unless a tenant's operator independently sets these three env vars — this ADR
  does not touch the managed-gateway Caddy topology or its own auth story.

## Rollback

- **Step A:** `git revert` the commit. Nothing to undo in the running environment beyond
  redeploying — Step A never removed anything, and unsetting the two env vars alone already
  returns to pre-ADR-0047 behavior without a code revert.
- **Step B:** setting `LOCAL_LOGIN_DISABLED=false` and redeploying restores local login
  immediately — no code revert needed, since Step B never deletes the login/setup/refresh
  handlers, it only gates them. A `git revert` of the Step B commit is also safe and
  independent of the Step A commit (separate, reviewable commits per the brief).

## What this ADR does NOT do

- Does not call the Cloudflare API, and does not create, modify, or inspect any Access
  application or policy. The existing app for `droneops.barnardhq.com` is left exactly as is.
- Does not deploy, push, or merge. Everything lands on `feat/operator-sso` in
  `~/wt-droneops-sso` only.
- Does not touch the customer client portal, intake, or TOS-acceptance auth paths
  (`app/auth/client_auth.py`) in any way — verified by grep, zero references to
  `cf_access`/`LOCAL_LOGIN_DISABLED` outside the operator surface.
- Does not enable `LOCAL_LOGIN_DISABLED` anywhere, including `docker-compose.bos-prod.yml` —
  that remains an explicit, separate operator action taken after the Step A soak.
- Does not regress the Phase 7 hardening that shipped the same day (`client_ip.py` trusted-
  proxy chain, TOS/intake/revocation fixes) — the full backend suite (895 passed / 23
  skipped, up from the pre-change 855/17 baseline verified at the start of this session; the
  8 new skips are the opt-in real-Postgres identity-resolution tier) and the frontend suite
  were both re-run after every change in this ADR.

## Alternatives considered

- **Trust the `Cf-Access-Jwt-Assertion` header without verifying its signature**, reasoning
  the BOS-HQ Docker network is private enough. Rejected outright — the origin is reachable
  from the whole WireGuard mesh; any container on it could forge the header. This is exactly
  what ADR-0246 decision 2 exists to prevent.
- **Resolve the CF-Access identity by matching `users.username` against the verified
  email.** Rejected — this is the precise defect a security review found in the marketing
  pilot's first draft, and this app's *lack* of a username charset restriction makes the
  collision surface worse here, not better.
- **Remove local login unconditionally** (delete the routes rather than gate them).
  Rejected — DroneOpsCommand is MIT-licensed, self-hostable software with a live public demo
  instance; neither has Cloudflare Access, and an unconditional removal would permanently
  lock every self-hoster out of their own instance. The gate/kill-switch design is the
  entire reason Step B could ship in the same session as Step A instead of being deferred
  to an operator-only follow-up.
- **A hand-rolled JWKS fetch with no cache.** Rejected — re-fetching Cloudflare's JWKS on
  every single request is both wasteful and a self-inflicted denial-of-service risk against
  our own backend under load; the TTL+cooldown cache (mirroring the marketing pilot's `jose`
  configuration) is the standard shape for this problem.
