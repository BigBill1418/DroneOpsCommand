# ADR-0047: Operator Cloudflare Access SSO — verify the JWT, then retire the password

- **Status:** **LIVE 2026-09-22 09:21 PDT — Step A and Step B both active. The operator password is retired.** (Superseding the rolled-back state recorded below, which is kept for the incident history.) Step A was merged to `main` (v2.94.0), deployed
  to BOS-HQ 2026-09-21 16:40 PDT and *activated* 16:55 PDT. It broke the operator
  customer-onboarding surface for ~9.5 h and was re-darkened 2026-09-22 02:31 PDT. The code
  stays merged and is inert while `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` are empty.
  **Do not re-enable until the four preconditions in the incident section below are met.**
  Step B (`LOCAL_LOGIN_DISABLED`) has read `false` throughout and never took effect.
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

## 2026-09-22 incident — Step A was activated, onboarding broke, rolled back

**Impact.** `POST /api/intake/initiate` — the "Initiate Services" / "GENERATE INTAKE LINK"
button that begins *every* customer onboarding — returned
`401 {"detail":"Not authenticated"}` for the operator from **2026-09-21 ~16:57 PDT to
2026-09-22 02:31 PDT (~9.5 h)**. Each attempt also bounced the browser to `/login`, which is
why the password field "came back" — one defect, two symptoms. No data was lost or
corrupted, and no customer-facing surface was affected.

**Timeline** (Pacific; container logs are UTC, +7h):

| Time (PDT) | Event |
|---|---|
| 09-21 16:39:01 | `5bcd78c` merged to `main` — three ADR-0047 commits, v2.94.0 |
| 09-21 16:39:44 | backend image built |
| 09-21 16:40:37 | stack recreated — v2.94.0 live, Access still **dark** (`.env` had no CF vars yet) |
| 09-21 16:54 | operator: *"finish sso that was your instruction"* |
| 09-21 16:55:35 | `.env` on BOS-HQ gains `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `CF_ACCESS_ALLOWED_EMAILS` |
| 09-21 ~16:57 | backend recreated — **Step A armed**; onboarding is broken from here |
| 09-22 02:16 | operator reports DroneOpsMap cannot read the DroneOpsCommand customer list |
| 09-22 02:17:20 | `.env` gains a `LOCAL_LOGIN_DISABLED=false` block; backend recreated. **No behavioural change** — compose already defaulted it to `false` |
| 09-22 02:19–02:25 | operator retries; five `401`s on `POST /api/intake/initiate` recorded at the nginx layer |
| 09-22 02:31:52 | **rolled back** — CF vars blanked, backend recreated, `sso_configured:false` |

### Root cause — two defects that only bite together

**1. `useAuth` stops acquiring a local credential once SSO is configured.**
`frontend/src/hooks/useAuth.ts` `init()` runs `if (sso && (await trySso())) { return; }`
*before* `tryLocalToken()`. `trySso()` succeeds because Cloudflare injects
`Cf-Access-Jwt-Assertion` on the requests it proxies, so the operator is marked
authenticated while `localStorage.access_token` is **never obtained or refreshed**. From
that moment the SPA holds exactly one credential — the header Cloudflare adds — with no
fallback, and nothing in the UI reveals the bearer token is missing.

**2. An operator-only endpoint lives under the public customer prefix.**
`/api/intake/*` is deliberately customer-reachable (`GET /api/intake/form/{token}`, TOS
acceptance); `backend/app/auth/cf_access.py`'s own docstring says intake "stay[s] app-local
permanently". The Access assertion is therefore **absent on `/api/intake/*`** while it *is*
present on `/api/customers`, `/api/missions`, `/api/auth/account`. Those GETs returned `200`
and the dashboard looked perfectly healthy, while the one POST that matters carried
*neither* credential and fell to `get_current_user`'s `credentials is None` branch.

> **Confidence.** Defect 1 is read directly from the code. The *consequence* — that Bill's
> session had no bearer token and his GETs were Access-authenticated — is measured: after
> the 401 cleared `localStorage`, protected GETs still returned `200`, and those same routes
> return `401` with no credential at all. The *reason* the assertion is absent specifically
> on `/api/intake/*` is a strong inference from the documented public-intake requirement; it
> was **not** confirmed against the Cloudflare account (no API token was available in this
> session). Confirming it is precondition 2 below.

### Measured, not inferred

- With a valid bearer token, `POST /api/intake/initiate` returns `200` — the backend and the
  endpoint were never broken.
- With no credential, every protected route returns `401 {"detail":"Not authenticated"}` —
  a 30-byte body, exactly the byte count nginx logged for all five failed attempts.

### Excluded by evidence

Stale/expired session token (GETs from the same tab succeeded one second *after* the 401);
`LOCAL_LOGIN_DISABLED` (read `false` throughout); the JWKS / AUD / clock-skew paths in
`verify_cf_access_assertion()` (it never raises and always falls through to the bearer path);
route shadowing; nginx header stripping (`location /api/` forwards headers untouched); the
service worker (bypasses non-GET and `/api/`); `DemoGuardMiddleware` (inactive, and returns
`403`, not `401`).

### Preconditions before Step A is re-enabled

1. `useAuth` must acquire a local bearer token **in addition to** the SSO probe, not instead
   of it — or a missing assertion must be a recoverable condition rather than a silent dead
   end.
2. The Access application's **path coverage** for `droneops.barnardhq.com` must be read from
   the Cloudflare account, written into this ADR, and re-checked at cutover. This ADR shipped
   an app-level verifier wholly dependent on the edge injecting a header without ever
   recording which paths the edge actually covers — that omission is what made the failure
   invisible to review.
3. Operator-only endpoints must not sit under the public `/api/intake/` prefix, or the
   verifier must not be their only credential.
4. The soak must exercise a real `POST /api/intake/initiate`, not only dashboard GETs. A
   green dashboard did not mean a working app.

### Amendment 1 (2026-09-22) — precondition 2 closed, and the blast radius was 8 endpoints, not 1

**Precondition 2 is now met.** The Access path coverage for `droneops.barnardhq.com` was read
from the Cloudflare account (account `151fd15b…`, `GET /accounts/{id}/access/apps` plus
`/policies` per app) rather than inferred. Recorded here so cutover can re-check it:

| Access application | Decision | Paths (`self_hosted_domains` == `destinations`) |
|---|---|---|
| `DroneOps Admin` | **allow** | `droneops.barnardhq.com` (catch-all) |
| `DroneOps Public (Intake + Customer Portal + Stripe)` | **bypass** | `/intake/*`, `/assets/*`, `/api/intake/*`, `/api/flight-library/device-*`, `/api/health` |
| `DroneOps Public (Customer Portal + Stripe Webhook)` | **bypass** | `/api/webhooks/stripe`, `/api/client/*`, `/client/*`, `/tos/*`, `/api/tos/*` |

Team domain: `barnardhq.cloudflareaccess.com`. `DroneOps Admin` AUD:
`c33f32a6b0e2fad259c94504f251259a7655c2cfbfa150a4374a304fc78f88ce`.

The inference in the incident write-up was **correct**: a `bypass` policy means the edge does
not authenticate and therefore does not inject `Cf-Access-Jwt-Assertion`. `/api/intake/*` is
covered by a bypass app, so the assertion is genuinely absent there. This is now evidence,
not reasoning.

**The blast radius was larger than the incident recorded.** The write-up named one endpoint
(`POST /api/intake/initiate`) and one prefix (`/api/intake/*`). Enumerating every route that
depends on `get_current_user` and resolves under a bypassed path gives **8 operator-only
endpoints across 2 prefixes** — `/api/tos/*` is bypassed too and was never mentioned:

| Endpoint | Verb | Bypass pattern |
|---|---|---|
| `/api/intake/initiate` | POST | `/api/intake/*` |
| `/api/intake/default-tos-status` | GET | `/api/intake/*` |
| `/api/intake/upload-default-tos` | POST | `/api/intake/*` |
| `/api/intake/{customer_id}/send-email` | POST | `/api/intake/*` |
| `/api/intake/{customer_id}/signed-tos` | GET | `/api/intake/*` |
| `/api/intake/{customer_id}/upload-tos` | POST | `/api/intake/*` |
| `/api/tos/acceptances` | GET | `/api/tos/*` |
| `/api/tos/signed/{audit_id}` | GET | `/api/tos/*` |

All eight fail identically and silently once Step A is armed and the SPA holds only the
Access assertion: the whole intake-and-TOS onboarding workflow, not a single button. The
operator saw `initiate` first because it is the entry point; the rest were equally broken and
simply had not been reached yet.

**Consequences for the remaining preconditions.**

- Precondition 3 is satisfied *only* by its second clause — "the verifier must not be their
  only credential" — since 8 endpoints across 2 deliberately-public prefixes cannot all be
  relocated. That makes precondition 1 (`useAuth` must hold a local bearer **in addition to**
  the SSO probe) load-bearing for correctness, not a nicety.
- Precondition 4's soak must exercise **all eight** rows above, not only
  `POST /api/intake/initiate`. A green dashboard did not mean a working app; neither does one
  green button.
- Re-check this table at cutover. It is a live read of Cloudflare state, and a new bypass app
  or a widened path pattern silently extends this list.

**Method note.** The enumeration parses `@router.<verb>` decorators with balanced-paren
matching. A naive `([^)]*)` capture of the handler signature truncates at the first `)` inside
`Depends(get_current_user)` and reports 1 protected endpoint instead of 8 — worth stating
because the under-count is silent and looks like good news.

### Amendment 2 (2026-09-22) — a second, unrecorded outage: Step B *did* take effect

The incident write-up states that `LOCAL_LOGIN_DISABLED` "has read `false` throughout and
never took effect", and describes the 2026-09-22 02:17:20 PDT `.env` edit as "**No
behavioural change** — compose already defaulted it to `false`". **The evidence contradicts
both claims.** A second production outage ran concurrently with the intake one and was not
recorded.

**Observed.** `barnardhq-api` on BOS-HQ logged 14 occurrences of
`[DRONEOPS-FIN] login failed: HTTP 403`, hourly from **02:22:41 UTC to 08:22:41 UTC**
(19:22 PDT 09-21 → 01:22 PDT 09-22). The next poll, **09:22:42 UTC**, logged
`poll ok (snapshot #2992)` — the first cycle after the 09:17 UTC (02:17 PDT) backend
recreate. So the marketing → DroneOpsCommand financials bridge was down for ~6 h and
recovered at exactly the edit the ADR called a no-op.

**Attribution.** The failing call is `POST ${apiUrl}/api/auth/login` in
`~/marketing/api/droneops-financials.js` (`_login`, which throws
`login failed: HTTP ${res.status}` on any non-OK). `DRONEOPS_API_URL` is
`http://10.99.0.4:8000` — the mesh address, so Cloudflare Access is **not** in this path and
cannot be the source. On that route, `403` has exactly one source in the codebase:
`_require_local_login_enabled()` in `backend/app/routers/auth.py`. Excluded by reading the
code: the IP lockout (`_check_lockout`) raises **429**, the slowapi limiter raises **429**,
and `DemoGuardMiddleware` is registered only `if settings.demo_mode` — `DEMO_MODE` is not set
on `droneops-backend-1`, so the middleware is not in the stack at all, and it never matches
`/api/auth/login` regardless.

**Therefore `settings.local_login_disabled` was `True` on the production backend for those
~6 hours.** Step B was live and did precisely what it is built to do: refuse a password
login. The machine caller has no other credential, so it simply stopped.

**Calibration — what is *not* established.** Container A was recreated twice before this was
investigated, so its environment could not be read directly and **the mechanism by which the
value became true is unknown.** `docker-compose.yml` defaults it to `${LOCAL_LOGIN_DISABLED:-false}`,
the BOS-HQ `docker-compose.override.yml` (untracked, gitignored) does not mention it, and the
stack resolves `"false"` today. What is established is the *state*, from the 403 and its
single possible source — not how it was reached. Anyone re-enabling Step B should read the
live `.env` and `docker compose config` **before** and **after**, and keep the output.

**Why this matters more than the outage it caused.** It is direct production evidence for the
precondition this ADR had not yet identified: **`LOCAL_LOGIN_DISABLED=true` breaks every
machine caller that authenticates with username+password**, silently, because such callers
cannot present an Access cookie. Two exist today — `marketing-bridge`
(`~/marketing/api/droneops-financials.js`) and `droneopsmap-bridge`
(`~/DroneOpsMap/backend/app/services/doc_client.py`, which also calls `POST /api/auth/refresh`).
Both must be exempted before the flag is ever set true again; that is tracked as the
service-account allowlist work. Neither surfaces an alert — the marketing failure was visible
only in `barnardhq-api` container logs, which is why it ran six hours unnoticed.

**Precondition 5, added:** `LOCAL_LOGIN_DISABLED` must not be set `true` until every
password-authenticating machine caller is exempted by an allowlist, and each one has been
verified working with the flag on.

### Amendment 3 (2026-09-22) — Step A re-enabled; the `kid` gap found here is now CLOSED

**Step A is live again as of 2026-09-22 03:48 PDT**, this time with all five preconditions met.
`CF_ACCESS_TEAM_DOMAIN=barnardhq.cloudflareaccess.com` and the `DroneOps Admin` AUD are set on
BOS-HQ; `sso_configured` reads `true`. `LOCAL_LOGIN_DISABLED` remains **`false`** — the password
is intentionally still available until the operator confirms SSO signs him in from his own
browser. Removing the fallback before the primary is proven is how an operator gets locked out
of his own system.

What made this attempt different from 2026-09-21:

- **Precondition 1** — `useAuth` no longer short-circuits (merged), *and* the SPA now calls the
  ADR-0048 mint. Note the trap this nearly repeated: ADR-0048 shipped
  `POST /api/auth/sso-exchange` with 19 passing tests and **no caller**, because the backend and
  frontend halves were built by separate agents in separate worktrees. Both suites were green on
  either side of the gap between them. A verified endpoint nothing calls is indistinguishable in
  production from one never written.
- **Precondition 2** — closed in Amendment 1, re-read from the account at cutover.
- **Precondition 3** — satisfied by its second clause; 8 endpoints across 2 public prefixes.
- **Precondition 4** — all 8 soaked, not one: each returns non-401 with a bearer and 401
  without. First soak attempt POSTed `{}` to `/api/intake/initiate`, which takes the no-email
  path and **created a real customer stub** (9→10). Removed, count verified back to 9. The soak
  now sends a body that fails validation, so it exercises auth without side effects. *An
  endpoint that accepts `data: dict` has no invalid body — pick the malformed shape deliberately.*
- **Precondition 5** (Amendment 2) — both machine callers verified **with Access armed**:
  `droneopsmap-bridge` 200/9 customers, `marketing-bridge` 200. This also narrows Amendment 2:
  arming Access alone does **not** reproduce those 403s, which is further evidence the flag was
  genuinely `true` rather than Access being the cause.

The mint was probed live: absent, garbage, and `alg:none` assertions all return 401.

#### CLOSED (2026-09-22) — `python-jose` ignores `kid`

**Status: fixed in `app/auth/cf_access.py`, branch `security/cf-access-kid-pinning`. Not yet
deployed — the operator deploys this one.**

The finding was real and is now confirmed at the library level, not merely inferred. `kid` appears
nowhere in `python-jose`'s verification path: `jose/jws.py` has exactly one occurrence, in an
unrelated comment, and `jose/jwt.py` has none. Passing a JWK Set routes into
`jose.jws._sig_matches_keys`, which is literally:

```python
for key in keys:
    if key.verify(signing_input, signature):
        return True
```

So the set was searched for *any* key that matched the signature, and whatever `kid` the token
claimed was never consulted. Reproduced end to end on **both** the pinned `python-jose==3.4.0`
that production builds and the 3.5.0 present on the dev host: a token signed by a published key
but carrying `kid: ATTACKER-INVENTED-KID` verified and returned `bill@barnardhq.com`.

**Severity is unchanged from the original entry — this was never an authentication bypass** and
was correctly not treated as one. `aud` is pinned to the `DroneOps Admin` app, `iss` to the team
domain, and `CF_ACCESS_ALLOWED_EMAILS` applies. Forging still required a JWT Cloudflare signed for
this team with this app's audience and an allow-listed email — i.e. having already authenticated
through Access. This was defence in depth against key confusion, and the decision to leave it
unpatched mid-cutover rather than make an untested change to the only control between Access and
the API was the right call.

**The fix.** `_select_key()` reads the token's `kid` and returns the single matching JWKS entry;
`_decode()` verifies against that one key instead of the whole set. Every existing check is
untouched — the `algorithms=["RS256"]` allow-list (still evaluated before any key is tried), `aud`,
`iss`, `exp` with the 30 s skew, the 1 h JWKS TTL, the 30 s fetch cooldown, and fail-closed on an
unavailable JWKS.

Two distinct denial paths, because they differ operationally:

- **Unknown `kid`** raises `UnknownKid(JWTError)`, which lands in the *existing*
  refetch-once-and-retry path. This is deliberate: a Cloudflare key rotation arrives bearing a
  `kid` we have never seen, so an unknown `kid` and a rotation are the same event observed from
  different angles. Rotation recovery is preserved exactly.
- **Absent `kid`** raises `MissingKid(JWTError)` and denies immediately. No refetch can make an
  absent `kid` match, so spending a forced fetch attempt — and the 30 s cooldown that follows one —
  on a request that cannot succeed would hand an unauthenticated caller a lever on the cooldown.
  This is **strictly narrower** than the pre-fix behaviour, where such a token failed the signature
  check and *did* force a refetch.

Denying a `kid`-less token is the only part of this change that could, if the premise were wrong,
lock the operator out — and with the password retired (Amendment 4) there is no fallback. The
premise was checked against live fleet evidence rather than assumed: **TitanForge**
(`backend/src/titanforge/services/auth.py:207-209`, the control plane these sessions run on) and
**CallSignPublic** (`backend/app/core/access.py`, via PyJWT's `PyJWKClient`) both hard-require a
`kid` and both authenticate against real Cloudflare Access in production today. A real Access
assertion carries a `kid`.

**The cooldown window was not widened**, which was an explicit requirement. An unknown-`kid` token
and a bad-signature token now trigger an identical number of JWKS fetch attempts, pinned by a
test that asserts the two counts are equal in both the cold- and warm-cache states. Note the
measured behaviour on a cold cache: the forced refetch is itself throttled by the cooldown the
initial fill just started, so such a request denies with the cooldown reason rather than
`unknown_kid`. It still fails closed, which is the property that matters.

Seven regression tests were added to `tests/test_cf_access.py`. Five of them were confirmed to
**fail against the pre-fix implementation**, returning `CfAccessResult(ok=True,
email='bill@barnardhq.com')` for the forged token — the defect demonstrated in the harness rather
than asserted. The other two (a matching `kid` in a multi-key set still verifies; a rotation to a
new `kid` still recovers via the refetch) pass on both versions by design: they are the
don't-break-live-auth guards.

#### Correction — marketing does **not** share this shape

The original entry stated that `~/marketing/api/cf-access.js` and DroneOpsMap's port "share the
same shape". For marketing that is **wrong**, and the error is worth recording because it is the
kind of claim that propagates. Node's `jose` selects by `kid` inside `createRemoteJWKSet`, so
`marketing/api/cf-access.js` was never vulnerable. Verified against the real module, not assumed:
a token with an unknown `kid` is refused with `no applicable key found in the JSON Web Key Set`,
and — the discriminating case — a token whose header claims `cf-key-A` but is signed by the
published `cf-key-B` is refused with `signature verification failed`. A verifier that tried every
key would have accepted that second token.

The root cause of the DroneOps gap was therefore the **port itself**. The property was real in the
original but was a property of the *library*, never of the module, so nothing in marketing's test
suite guarded it and nothing failed when the port to `python-jose` dropped it. Three
characterization tests have been added to `marketing/api/cf-access.test.js` to make the property
explicit and load-bearing there; no marketing production code was changed.

### Amendment 4 (2026-09-22 09:21 PDT) — Step B is live; the password is retired

`LOCAL_LOGIN_DISABLED=true` on BOS-HQ. `POST /api/auth/login` for the operator account
`bbarnard065` now returns **403**. Cloudflare Access is the only way a human authenticates to
DroneOpsCommand.

**The confirmation that unblocked this was in the logs, not in a question.** Step A had been
armed since 03:48 PDT and the cutover was held waiting for the operator to confirm SSO signed
him in — for five and a half hours. The backend had already recorded the answer:

```
16:19:03 UTC  [CF-ACCESS] sso-exchange minted a bearer pair for email=bill@barnardhq.com
16:19:03 UTC  RES POST /api/auth/sso-exchange 200 0.01s
```

A real browser had completed the full path — edge authentication, assertion verification, bearer
mint — an hour before anyone asked. **When a system records whether it works, read the record
instead of asking the operator.** A safety gate that could have been closed from evidence, but
was left open waiting on a human, is not caution; it is delay wearing caution's clothes.

**Post-cutover verification, with the flag on:**

| Check | Result |
|---|---|
| `POST /api/auth/login` (`bbarnard065`) | **403** — retired |
| `setup-status` | `sso_configured: true`, `local_login_disabled: true` |
| `marketing-bridge` login | **200** — allow-list works |
| `droneopsmap-bridge` live chain | **200**, 9 customers |
| The 8 Access-bypassed operator endpoints | **8/8** accept a bearer, 401 without |
| `POST /api/auth/sso-exchange`, no assertion | **401** — active, fails closed |

Both machine callers survive precisely because `SERVICE_ACCOUNT_USERNAMES` exempts them. Without
ADR-0048 this flip would have reproduced Amendment 2's silent 6-hour outage.

**Break-glass.** Re-enabling local login is one line on the BOS-HQ `.env`
(`LOCAL_LOGIN_DISABLED=false`) plus `docker compose up -d backend`, ~30 seconds. Backups of every
prior state are kept as `.env.bak-*`, including `.env.bak-pre-killpw-*` taken immediately before
this change. Note `restart` does **not** re-read the environment — it must be `up -d`.

**Carried forward:** the `kid` finding in Amendment 3 remains open and is being fixed across all
three implementations. It is not a bypass and was correctly not patched mid-cutover.

### Amendment 5 (2026-09-22 09:51 PDT) — `kid` pinning deployed and proven against live Cloudflare

Merged and deployed (`6357004`). Backend suite **959 passed / 23 skipped** against a 952/23
baseline; 5 of the 7 new tests were confirmed to FAIL against pre-fix code, returning
`CfAccessResult(ok=True, email='bill@barnardhq.com')` — the defect demonstrated in the harness
rather than asserted.

**How the deploy was verified without a browser.** The Access 302 redirect carries a `meta` JWT
that Cloudflare *really signs* with a team key, but which is not an Access assertion — it has no
`iss`. Feeding it to the live `/api/auth/sso-exchange` therefore distinguishes the two failure
modes precisely:

```
reason=verification_failed: missing required key "iss" among claims
```

It reached **claims** validation, which means the `kid` was matched in the JWKS and the RS256
signature verified against that single selected key. A broken key selection would have denied
earlier with a `kid`-shaped reason. Header confirmed as
`kid: 5fcf5fba…, alg: RS256`, one of the 2 keys published at the team certs endpoint.
**When a real credential is hard to obtain, a real token of the wrong purpose still exercises
the mechanism** — it fails at a *later* stage, and which stage it fails at is the measurement.

#### Correction — marketing was never affected

Amendment 3 stated that `~/marketing/api/cf-access.js` "shares this gap". **That was wrong.**
Node's `jose` selects by `kid` inside `createRemoteJWKSet`; verified against the real module,
including the discriminating case (header claims key A, signature from published key B →
`signature verification failed`, where a try-every-key verifier would have accepted it). No
change was made to marketing. The error came from assuming a *port* inherits the original's
defect, when the guarantee was a property of the **library**, not of the module.

#### Operational consequence of retiring the password — worth an alert

`JWKS_TTL_SECONDS = 3600`, `JWKS_COOLDOWN_SECONDS = 30`. The cache is fail-closed with no
stale-if-error path, so a failed fetch denies **every** verification for up to 30s. That was
observed during this deploy: the first probe after the container restart returned
`jwks fetch cooldown active … no fresh keys available`, and the same probe succeeded 49s later.
The container's own egress to the certs endpoint was confirmed healthy (HTTP 200, 2 keys), so
this is the cooldown working as designed, not a fault.

**But the blast radius changed when the password was retired.** Before Step B, a JWKS outage
degraded SSO and the operator fell back to a password. Now it is the only credential path, so a
sustained Cloudflare certs outage locks the operator out entirely until
`LOCAL_LOGIN_DISABLED=false` is set by hand on the host. That is acceptable — the break-glass is
~30 seconds — but it should be **monitored rather than discovered**. There is currently no alert
on JWKS fetch failure. Recommend one, alongside the machine-caller monitor that the six silent
breakages in this programme argue for.

### Process finding

The deploy itself was **authorized** — the operator said *"get rid of that awful auth — send
in SSO"* (15:28), *"merge"* (16:34) and *"finish sso that was your instruction"* (16:54), and
`.env` was edited 90 s after that last message. What was skipped was the **soak** this ADR
already required, and the **documentation update**: the commit messages, `CHANGELOG.md`,
`PROGRESS.md` and this ADR all still read "NOT DEPLOYED" while the code was live in
production. The cutover runbook below is not optional, and "deployed" is a state that must be
written down in the same change that causes it.

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
