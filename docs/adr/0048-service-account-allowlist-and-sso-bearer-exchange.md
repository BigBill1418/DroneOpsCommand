# ADR-0048: Two things must exist before the password can be retired — a service-account allow-list, and an SSO→bearer exchange

- **Status:** Accepted, merged, **not enabled**. Both mechanisms ship inert: the allow-list is
  empty by default and the exchange endpoint answers `404` until Cloudflare Access is
  configured. Neither changes any observable behaviour on a self-hosted/OSS install, on the
  public demo instance, or on BarnardHQ production as currently configured.
- **Date:** 2026-09-22
- **Follows:** `0047-operator-cloudflare-access-sso.md`, specifically its **Amendment 1**
  (commit `02fa150` — the blast radius is 8 operator-only endpoints across 2 bypassed
  prefixes, not 1 endpoint) and **Amendment 2** (commit `9bbc675` — `LOCAL_LOGIN_DISABLED`
  *was* true in production for ~6 h and silently killed the `marketing-bridge` financials
  poller; recorded there as **precondition 5**).
- **Under:** noc-master `docs/adr/0246-fleet-auth-posture-standard-and-the-ip-bypass-decision.md`
  decisions 1, 2 and 5.
- **Does not change:** `LOCAL_LOGIN_DISABLED` anywhere. That remains an operator flip on the
  host, and ADR-0047's preconditions still gate it.

## Context

ADR-0047 Step B added a switch that retires local password login. It is still `false` because
turning it on breaks things — and by 2026-09-22 the evidence said it breaks *two* separate
things, for two unrelated reasons. Both are documented in ADR-0047's amendments; this ADR is
the code that closes them.

**Gap 1 — machine callers have no other credential.** Two repositories log into this API over
the WireGuard mesh with username and password, and cannot present a Cloudflare Access cookie
because Access is not in that network path at all:

| Caller | Code | Routes used | Account |
|---|---|---|---|
| DroneOpsMap | `~/DroneOpsMap/backend/app/services/doc_client.py` | `POST /api/auth/login`, `POST /api/auth/refresh` | `droneopsmap-bridge` |
| Marketing dashboard | `~/marketing/api/droneops-financials.js` | `POST ${apiUrl}/api/auth/login`, `/api/auth/refresh` | `marketing-bridge` |

This is not hypothetical. ADR-0047 Amendment 2 established from `barnardhq-api` logs that the
flag was true in production for ~6 h on 2026-09-21, that the financials bridge logged
`[DRONEOPS-FIN] login failed: HTTP 403` fourteen times, and that **nothing alerted**. Amendment 2
records it as precondition 5: the flag must not be set true until both callers are exempted and
verified working with it on.

**Gap 2 — an SSO-only operator cannot obtain a bearer token at all.** Access is an *edge*
control. Amendment 1 read the live Cloudflare account and found three Access apps covering
`droneops.barnardhq.com`, two of them with **bypass** policies over `/api/intake/*`,
`/api/tos/*`, `/api/client/*` and others. On a bypassed path the edge does not authenticate and
therefore injects no `Cf-Access-Jwt-Assertion`, so the **8 operator-only endpoints** under those
prefixes accept only a local bearer. Fixing the SPA's `useAuth.init()` ordering (ADR-0047
precondition 1) lets it *keep* a local bearer it already has — but nothing can **mint** one once
`POST /api/auth/login` answers 403. Enabling Step B with only that fix in place re-creates the
~9.5 h onboarding outage of 2026-09-21, because the entire intake-and-TOS workflow depends on a
credential that no longer has a source.

The two gaps share a shape: **Step B removes the only issuer of local bearer tokens, and two
distinct classes of legitimate caller had no other way to get one.** So this ADR adds exactly
two issuers back — one for machines, one for the SSO operator — and nothing else.

## Decision

### 1. `SERVICE_ACCOUNT_USERNAMES` — a named exemption from the Step B 403

A comma-separated, empty-by-default setting (`backend/app/config.py`), wired through
`docker-compose.yml` as `SERVICE_ACCOUNT_USERNAMES=${SERVICE_ACCOUNT_USERNAMES:-}` and parsed
by `settings.service_account_allowlist` into a normalised `frozenset[str]`.

`_require_local_login_enabled()` in `backend/app/routers/auth.py` gains an optional
`exempt_username` argument. Its four call sites split deliberately:

| Route | Passes | Behaviour when Step B is on |
|---|---|---|
| `POST /api/auth/setup` | *nothing* | **403 always** — this route CREATES a credential |
| `POST /api/auth/login` | `body.username` | allow-listed names proceed to the password check |
| `PUT /api/auth/account` | *nothing* | **403 always** — this route MUTATES a credential |
| `POST /api/auth/refresh` | the decoded `sub` | allow-listed subjects proceed |

Design properties, each pinned by a test:

- **The exemption is from the 403 and from nothing else.** An allow-listed account still fails
  on a wrong password (401), still trips the per-IP lockout after 5 failures (429), and is still
  refused when `is_active` is false (401). The allow-list buys the right to *present* a
  password, never the right to skip checking it.
- **No privilege grant, structurally.** `users` has no role or admin column at all
  (`backend/app/models/user.py`) — the same fact ADR-0047 relies on for its shadow users. There
  is nothing for an allow-list entry to escalate to.
- **Fail closed.** Unset, blank, or a value that is nothing but separators all parse to an empty
  set, and an empty set exempts nobody. Omitting the argument at a call site is the fail-closed
  default, so a future route that forgets to opt in is hard-blocked rather than open.
- **Case-sensitive, and not stripped at the call site.** `User.username` is a Postgres `varchar`
  compared byte-exact. Lower-casing the list (as `cf_access.py` correctly does for e-mail
  addresses, which are case-insensitive by RFC) would wave a name past the gate that the very
  next query could never find.
- **No user-enumeration regression.** The guard runs *before* the row is read, on both routes,
  so a non-allow-listed name gets a byte-identical 403 whether or not that account exists.
- **Refresh takes its subject from the signed token**, after signature, `type` and `sub`
  validation — never from a request field. A body claiming an allow-listed `username` alongside
  a token whose `sub` is `admin` is refused.

### 2. `POST /api/auth/sso-exchange` — a verified Access assertion becomes a local bearer

A new endpoint that trades a cryptographically verified `Cf-Access-Jwt-Assertion` for the same
`{access_token, refresh_token, token_type}` pair `POST /api/auth/login` returns — same claims,
same helpers, same expiry — so nothing downstream in the SPA changes.

- **It is deliberately NOT behind `_require_local_login_enabled()`.** It is not a local login:
  no password is presented, accepted or created. It must work *precisely* when local login is
  disabled; that is its entire purpose.
- **Verification, not header trust.** The origin is reachable from the WireGuard mesh at
  `10.99.0.4:8000`, where any container can set any header. The endpoint calls the existing
  `verify_cf_access_assertion()`, which checks an RS256 signature against Cloudflare's JWKS for
  this team domain with an explicit algorithm allow-list, and requires `aud`, `iss` and `exp`.
  A forged, unsigned (`alg: none`), algorithm-confused (HS256-with-the-public-key), expired,
  wrong-`aud` or wrong-`iss` assertion mints nothing.
- **`CF_ACCESS_ALLOWED_EMAILS` still applies** — defence in depth independent of the Cloudflare
  policy, inherited unchanged from the verifier.
- **Identity comes from `resolve_cf_access_user()`**, the same mapping-table resolver
  `get_current_user` already uses. No second identity mechanism, and a first-time operator is
  provisioned through the same path with the same shadow row.
- **Structurally dark when unconfigured.** With `CF_ACCESS_TEAM_DOMAIN` or `CF_ACCESS_AUD`
  empty, it answers `404` before it reads the header — chosen over `403` so that on an install
  with no Access the route looks absent rather than advertising a door. OSS, self-hosted and the
  public demo instance can never reach the token-minting path.
- **Denials do not say which check failed.** `email_not_allowed` and `token_expired` produce an
  identical body; the reason goes to the log only. Otherwise the endpoint is an allow-list
  oracle.
- Rate limited at `10/minute` per trusted client IP, matching `/login`, bounding both JWKS
  churn and the bcrypt cost of first-time shadow-user provisioning.

## Consequences

- **Step B's two known breakages are closed in code.** Whether it is now *safe* to enable
  remains an operator judgement under ADR-0047's preconditions — this ADR closes precondition 5
  outright and supplies the missing mechanism precondition 1 needs to be sufficient.
- **One deliberate behaviour change, on a path no real caller takes.** `_require_local_login_enabled()`
  used to be the first statement in the `refresh` handler; it now needs a subject only the
  decode can establish, so it moved below the decode. Therefore: with Step B on, an
  **undecodable** refresh token now answers `401` instead of `403`. The token failed signature
  verification, so nothing about any user is revealed, and `GET /api/auth/setup-status` already
  publishes the flag. Every path carrying a decodable token — which is every path a real caller
  takes — is unchanged. Pinned by a test so it stays deliberate.
- **An allow-list entry is a live password credential.** Naming an account here keeps its
  password a working key to this API while every human credential is retired. The two entries
  must stay non-interactive machine accounts, and rotating their passwords stays an ordinary
  operational task. Under the fleet B2 retention policy (global CLAUDE.md), a leaked value is
  handled by rotating **and** treating the old one as compromised.
- **`sso-exchange` is a token issuer, and is only as strong as the Access app in front of it.**
  Its security rests entirely on Cloudflare's signature plus `CF_ACCESS_ALLOWED_EMAILS`. If the
  `DroneOps Admin` Access app were ever changed to a `bypass` policy, Cloudflare would stop
  injecting assertions and the endpoint would simply mint nothing — it fails closed in that
  direction. Amendment 1's app/policy table is a live read of Cloudflare state and must be
  re-checked at cutover.
- **Neither mechanism alerts.** As Amendment 2 notes, a broken machine caller is silent. Nothing
  here changes that; a monitor for it remains unbuilt and is worth a follow-up.

## To enable (operator, on the host — not done by this change)

1. Set on BOS-HQ's `.env`, then recreate the backend:
   `SERVICE_ACCOUNT_USERNAMES=marketing-bridge,droneopsmap-bridge`
2. Verify with Step B still **off** that both bridges keep working, and confirm the rendered
   value with `docker compose config` (per Amendment 2: read the live env *before* and *after*,
   and keep the output).
3. Only then work ADR-0047's remaining preconditions for `LOCAL_LOGIN_DISABLED=true`, and
   exercise all **8** endpoints from Amendment 1's table plus a real `sso-exchange`, not just
   dashboard GETs.

## Rollback

Both are config, not code deletions. Clearing `SERVICE_ACCOUNT_USERNAMES` restores the exact
pre-ADR-0048 Step B behaviour; blanking `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` returns
`sso-exchange` to `404`. Setting `LOCAL_LOGIN_DISABLED=false` restores password login for
everyone regardless of either.

## Verification

Full backend suite on this branch: **952 passed, 23 skipped** (baseline on the same checkout
before the change: **907 passed, 23 skipped**). The 45 new tests live in
`backend/tests/test_local_login_disabled.py` (+26) and `backend/tests/test_sso_exchange.py`
(19, new file).

Each claim above was falsified by mutating the implementation and confirming the *specific*
test goes red — a green test that cannot fail proves nothing:

| Mutation | Tests that went red |
|---|---|
| Guard ignores the exemption | 8 (every allow-listed login/refresh case) |
| Allow-list parse lower-cases entries | `test_allowlist_parse_preserves_case` |
| Allow-list matching made case-insensitive | `test_allowlist_match_is_case_sensitive` |
| `setup` passes the attempted username | `test_setup_still_403s_for_an_allowlisted_name` |
| `update_account` passes the authenticated username | `test_account_put_still_403s_for_an_allowlisted_service_account` |
| `refresh` trusts a client-supplied subject | `test_refresh_subject_comes_from_the_token_not_the_request_body` |
| **`sso-exchange` trusts the header unverified** | **9**, including the forged-key, `alg:none` and HS256-confusion cases |
| `sso-exchange` drops the configured-check | both `404` tests |
| `sso-exchange` gated by `_require_local_login_enabled()` | both "works when disabled" tests |
| `CF_ACCESS_ALLOWED_EMAILS` check removed | `test_email_outside_the_allowlist_mints_nothing`, `test_denial_does_not_disclose_which_check_failed` |

The `sso-exchange` tests do **not** mock `verify_cf_access_assertion`. A real RSA keypair is
generated per module and every assertion is genuinely signed and genuinely verified; only the
JWKS *transport* is stubbed, so the signature, algorithm allow-list, `aud`, `iss` and `exp`
checks all really run. The `alg: none` and HS256-confusion tokens are assembled byte-by-byte
because `jose.jwt.encode` refuses to produce them — building those through the library would
have silently tested nothing.

## Method note

Two traps worth recording, both hit during this change:

1. **The `@limiter.limit` decorator binds the module-level `Limiter` at import time.** Giving
   each test app its own `app.state.limiter` does *not* isolate the rate-limit budget, so a test
   module that exceeds 10 logins/minute in aggregate starts failing on whichever test happens to
   run when the shared budget runs out — a failure that looks like a logic bug and moves around.
   Measured, not assumed, after six tests went red for this reason.
2. **Two layers answer `429` on `/api/auth/login`** — the per-IP lockout and the slowapi limiter.
   A test asserting only the status code passes whether or not the layer it names ever ran. The
   lockout test therefore also asserts on the body (`detail` vs slowapi's `error`).

## Amendment 1 (2026-09-22) — the endpoint needed a caller

This ADR shipped `POST /api/auth/sso-exchange` with full test coverage, and **no caller.** The
backend and frontend halves were built concurrently in separate worktrees; the frontend agent
finished first, correctly refused to invent a backend affordance, and left a comment saying the
exchange endpoint "does not exist today". Both statements were true when written and both were
false once this ADR merged.

Arming Access at that point would have reproduced the 2026-09-21 outage in full: the mint
existed but nothing invoked it, so an SSO-only operator still held no bearer and the 8
endpoints under the bypassed `/api/intake/*` and `/api/tos/*` prefixes still 401'd. **A
verified, well-tested endpoint that nothing calls is indistinguishable in production from an
endpoint that was never written** — the tests were green on both sides of a gap that ran
between them.

`useAuth.ts` now calls it on a verified Access session. Writing the regression test also caught
a real defect in the first wiring: running `tryLocalToken()` after a successful exchange sends
the newly minted token through a validation whose failure branch deletes it. `init()` now takes
exactly one path to a bearer.

**Carry forward:** when work is split across agents or repos, the integration point between
them is the thing no one's test suite covers. Check the caller exists, not just the callee.
