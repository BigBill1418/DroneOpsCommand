# CS-Public patch hand-off — Wave 2B item 5

**Target repo:** `~/repos/CallSignPublic` (confirmed via `git remote -v`:
`https://github.com/BigBill1418/CallSignPublic.git`, reachable, tree clean at
hand-off — commit `6c22708`). **Not** `~/callsign` (that's the private
CallSign tool, a different product).

**Why this lives here instead of a commit in that repo:** the Wave 2B
dispatch instructed not to create a worktree in CallSignPublic myself
("tell me and I will create a worktree for it"). This is a fully reviewed,
locally-verified patch, staged for whoever creates that worktree.

## What's here

- `0075-cspublic-search-origin-auth.patch` — unified diff, two files:
  `backend/app/api/archive.py` and `worker/api/src/index.ts`. Verified with
  `git apply --check` against the live checkout at hand-off time (clean,
  read-only check — no writes made to that repo).
- `0075-cspublic-search.test.ts` — new Worker test, goes at
  `worker/api/test/search.test.ts`. **Run and passing** (3/3), plus all 21
  pre-existing `push.test.ts` cases re-run against the patched `index.ts`
  (21/21 still pass) and `tsc --noEmit` clean — all verified in an isolated
  `/tmp` copy with `node_modules` symlinked from the real checkout, never
  touching the tracked files.
- `0075-cspublic-test_archive_search_origin_auth.py` — new origin test,
  goes at `backend/tests/unit/test_archive_search_origin_auth.py`. Mirrors
  `test_ingest_transmission.py`'s coverage of `_require_receiver_bearer`
  one-for-one for the new `_require_worker_bearer`. **NOT run** — this
  repo's Python test suite needs its own venv/fixtures I did not set up
  without a worktree. Mark UNVERIFIED until run for real.

## The defect

`cs-api.barnardhq.com/api/archive/search` — this origin's own tunnel
hostname — answers `GET /api/archive/search` directly and unauthenticated.
The cloudflared ingress allowlist has to admit that path (the Worker itself
fetches through it), so nothing at the tunnel layer blocks a direct hit.
Turnstile (Layer 2) and the per-IP rate limiter (Layer 3) in
`worker/api/src/index.ts` are edge-only — applied solely to traffic that
came through `cs.barnardhq.com` → this Worker → origin. A caller who finds
`cs-api.barnardhq.com` skips both.

## The fix

Mirrors this repo's own established, tested pattern for the receiver ingest
path (`backend/app/api/ingest.py`'s `_require_receiver_bearer`): a
constant-time Bearer, checked at the origin, sourced from the settings
store (same encrypted-at-rest mechanism as
`audio.transmission_ingest_token`), 503 when unconfigured rather than a
silent pass-through.

- `worker/api/src/index.ts`: new optional `Env.ORIGIN_SEARCH_SECRET`.
  `handleSearch` forwards `Authorization: Bearer <secret>` on its fetch to
  the origin — a **fixed, per-deployment value**, never derived from the
  caller's own request, so it does not affect the edge cache key.
- `backend/app/api/archive.py`: new `_SEARCH_ORIGIN_TOKEN_KEY =
  "search.worker_origin_token"` settings-store key and
  `_require_worker_bearer(authorization)`, called as the FIRST thing
  `archive_search` does, before any query work.

Deliberately **not** the same secret as `SEARCH_PASS_SECRET` (browser-facing
HMAC pass that lets a solved Turnstile challenge cover repeat searches) —
that one is designed to reach the client; this one must never leave the
Worker.

## MUST READ before merging — this is fail-closed by design

**Deploying this patch without first provisioning BOTH secrets takes down
archive search entirely, including through the legitimate
`cs.barnardhq.com` path.** The origin returns 503
(`search_origin_auth_not_configured`) for every request until
`search.worker_origin_token` is set.

Correct rollout order:

1. **Origin first:** set `search.worker_origin_token` to a fresh random
   value via the operator Settings UI (same place
   `audio.transmission_ingest_token` lives). Deploy the origin patch.
   At this point the origin 503s ALL search — expected, brief.
2. **Worker second:** `wrangler secret put ORIGIN_SEARCH_SECRET` in
   `worker/api/` with the **same** value, then `wrangler deploy`.
3. **Verify:** `curl https://cs.barnardhq.com/api/archive/search?date_from=<today>&date_to=<today>`
   returns a normal result (through the Worker); a direct
   `curl https://cs-api.barnardhq.com/api/archive/search?...` with no
   Authorization header returns 401 (proving the origin is no longer
   answering unauthenticated).

Rollback order is reversed: revert the Worker deploy (or clear
`ORIGIN_SEARCH_SECRET`) before reverting the origin, so the Worker is never
sending a header state the origin doesn't expect. In practice either order
is harmless — an unrecognized/missing header is handled by both old and new
code without crashing — but this keeps the brief-503 window on the safe
side during rollback too.

## What this does NOT touch

No Cloudflare Access policy, Worker route, Configuration Rule, or tunnel
ingress change — all out of scope per the Wave 2B dispatch (another agent
holds the Cloudflare Access API). The tunnel ingress allowlist admitting
`/api/archive/search` is unchanged and correct; the fix stands alone at the
application layer as instructed.
