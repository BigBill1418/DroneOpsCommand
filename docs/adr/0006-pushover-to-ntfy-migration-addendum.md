# ADR-0006 — Pushover → ntfy transport migration (addendum)

- **Status:** Accepted — 2026-04-25
- **Date:** 2026-04-25
- **Authors:** aegis (executor)
- **Scope:** DroneOpsCommand `backend/app/services/pushover.py` →
  `backend/app/services/ntfy.py`; admin/auth call sites; compose env
  wiring; tests
- **Strategic frame:** [NOC-Master ADR-0036](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/adr/0036-pushover-to-ntfy-migration.md)
- **Linked plan:** [NOC-Master `2026-04-25-pushover-to-ntfy-migration.md`](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/plans/2026-04-25-pushover-to-ntfy-migration.md)
- **Related ADRs (this repo):**
  - [`0002-droneopssync-upload-auth.md`](./0002-droneopssync-upload-auth.md) §5
    — silent-drift watchdog (4 layers; layer 3 + 4 are alert publishers)
  - [`0003-zero-touch-device-key-rotation.md`](./0003-zero-touch-device-key-rotation.md) §2.6
    — single FYI alert per rotation
- **Soak-pause:** `~/noc-master/data/soak-pause/droneopscommand.pause`
  set 2026-04-25 with `until=2026-05-03T00:18:53Z`. NOC deployer pulls
  this branch but will not redeploy until cleared.

---

## Context

ADR-0036 in NOC-Master decided to migrate the fleet from Pushover to
self-hosted ntfy. That ADR is the strategic frame for the entire fleet;
this addendum documents the DroneOpsCommand-specific implementation
choices, with particular attention to **what does not change** —
because the watchdog contract this module participates in (ADR-0002 §5
+ ADR-0003) is load-bearing for operator safety, and the migration
must not silently weaken it.

The DroneOpsCommand `pushover.py` module is:

1. The publisher for ADR-0002 §5 layer 3 (Celery beat
   `check_device_silence_task`), the hourly silent-drift watchdog
   that catches a controller whose Capacitor `Preferences` got wiped
   and is no longer reaching the backend.
2. The publisher for ADR-0002 §5 layer 4 (`auth/device.py`
   `validate_device_api_key`), the first-401 alert that catches a
   controller still trying with a revoked or post-rotation key.
3. The publisher for ADR-0003 §2.6 (`admin_device_rotation.py`
   `rotate_device_key`), the single FYI alert that fires after a
   zero-touch key rotation completes.

All three call sites depend on:

- **Identical function signatures.** Both `send_alert(...)` async and
  `send_alert_sync(...)` sync are called from existing code. Changing
  their parameter shapes would touch three more files for no gain.
- **Redis-backed dedup.** A long outage must not generate 50 alerts.
  Layer 4 in particular dedups by `(key_prefix, ip)` for one hour;
  layer 3 dedups by `device_silence:{device_id}` for 12 hours.
- **Fail-open Redis behaviour.** If Redis is unavailable when an alert
  fires, the dedup check is skipped and the alert sends anyway. Bill
  explicitly prefers a duplicate alert to a silent drop during the
  exact failure modes the watchdog exists to surface.
- **Best-effort transport.** A transport failure is a logged WARN that
  never blocks the request path. Workers and API handlers continue.

## Decision

Replace `app.services.pushover` with `app.services.ntfy`. Preserve
every observable contract above. Switch the wire format and transport
target only.

### Public API (preserved)

```python
async def send_alert(
    title: str,
    message: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl_seconds: int = 3600,
    priority: int = 0,
    # NEW (optional, opt-in per ADR-0036 standard) ↓
    topic: Optional[str] = None,
    click: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> bool: ...

def send_alert_sync(...) -> bool: ...   # same shape as above
```

The `topic`, `click`, and `tags` keyword arguments are new and
strictly opt-in — every existing call site continues to work without
changes, falling back to the defaults below.

### Transport (changed)

| Aspect            | Before (pushover)              | After (ntfy, ADR-0036)                                          |
|-------------------|--------------------------------|-----------------------------------------------------------------|
| Endpoint          | `https://api.pushover.net/...` | `https://ntfy.barnardhq.com/<topic>` primary                    |
| Auth              | `token` + `user` form fields   | `Authorization: Bearer <publisher-token>` header                |
| Title format      | Caller-shaped, free-form       | `[DroneOpsCommand] <summary>` (prepended by helper)             |
| Click URL         | Optional, per-call             | Per ADR-0036 3-tier priority; default falls back to NOC `/status/droneops` |
| Priority          | `int -2..2`                    | `int -2..2` from caller, mapped to ntfy `low/default/high/urgent` |
| Tags              | Not supported                  | Optional comma-joined header (`Tags: warning,device_silence`)   |
| Fallback          | None — silent drop on outage   | `https://ntfy.sh/barnardhq-fleet-droneops-<obscured>` with `[FALLBACK]` title prefix |
| Timeout budget    | 5 s                            | 5 s primary + 5 s fallback (worst case 10 s)                    |
| Env vars          | `PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY` | `NTFY_DRONEOPS_PUBLISHER_TOKEN` (single token)          |
| Dedup key prefix  | `doc:pushover:dedup:`          | `doc:pushover:dedup:` (preserved across cutover so in-flight dedup entries survive) |

### Watchdog contract preservation

This is the explicit checklist verifying ADR-0002 §5 + ADR-0003 alert
behaviours are unchanged:

- ADR-0002 §5.3 layer 3 (silence watchdog, Celery beat hourly)
  - Dedup key `device_silence:{device_id}` — preserved
  - TTL `DEVICE_SILENCE_DEDUP_HOURS` (default 12 h) — preserved
  - Title shape `DroneOps — <label> silent for <hours>h` — preserved
    (gets the `[DroneOpsCommand] ` prefix from the helper, per the
    new fleet standard, so the on-phone notification reads
    `[DroneOpsCommand] DroneOps — M4TD silent for 50h`)
  - Suppression on Pushover-unset → now suppression on
    `NTFY_DRONEOPS_PUBLISHER_TOKEN`-unset, same fail-soft semantics

- ADR-0002 §5.4 layer 4 (first-401 in `auth/device.py`)
  - Dedup key `device_auth_failed:{key_prefix}:{ip}` — preserved
  - 1 h TTL — preserved
  - "alert only on `/device-` paths" gate — preserved (call site
    code unchanged)

- ADR-0003 §2.6 rotation FYI (`admin_device_rotation.py`)
  - No dedup (every rotation is worth knowing about) — preserved
  - Title `DroneOps key rotated` — preserved
  - Best-effort behaviour (Pushover failure does not fail rotation)
    — preserved; the catch block now logs `rotate_key_alert_failed`
    instead of `rotate_key_pushover_failed` (transport-agnostic)

### New failure mode

A transport call now has **two** ways to fail before it gives up:

1. Self-hosted ntfy on BOS-HQ unreachable / 5xx → fall through to
   public `ntfy.sh` with a `[FALLBACK]` title prefix on the obscured
   per-service topic.
2. Both unreachable → log `ntfy alert dropped (primary + fallback both
   failed)` and return `False`.

This is **strictly better** than the prior failure mode (silent drop
on the first transport failure). The fallback path requires no
operator action — Bill's phone subscribes to both topics at install
time per ADR-0036.

### Non-goals for this commit

- **Run-time cutover.** Soak-pause holds the running container at
  v2.63.11. Operator clears the pause and pulls the new image when
  ready. Code change ships now; live traffic switches later.
- **Pushover key rotation.** ADR-0036 owns the fleet-wide
  decommissioning of `pushover_token` / `pushover_user_key` from
  `~/noc-master/data/config.yml:763-764` and the per-host
  `~/.pushover.env` files. This addendum only removes the env-var
  references from this repo's `docker-compose.yml`.
- **NOC `/status/droneops` route.** ADR-0036 §Components item 5 owns
  it. Until that route is live, the default click URL still resolves
  (NOC root), and individual alerts can opt in to better tier-1 / tier-2
  URLs via the new `click=` parameter as we touch each call site
  during routine work.
- **Migrating call sites to use the new `tags`/`topic`/`click`
  parameters.** Out of scope for this commit. They are accepted but
  unused by the existing call sites; future work can pass them
  per-alert without an API change.

## Consequences

### Positive

- One env var (`NTFY_DRONEOPS_PUBLISHER_TOKEN`) replaces two
  (`PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY`).
- Out-of-band fallback path means an outage of the self-hosted ntfy
  on BOS-HQ does not silently drop alerts — they take the public
  topic. This was the headline gap in Pushover.
- Title format is now uniform across the fleet (`[<Display Name>]
  <summary>`), per ADR-0036 §Notification standard.
- Click URL contract is enforceable: every alert that does not pass
  `click=` lands on the NOC status page rather than a "tap goes
  nowhere useful" dead end.
- Authorization header is `Bearer` instead of body-formed key+user
  fields — closer to industry norm, easier to reason about.

### Negative / cost

- One more place to audit when adding alert call sites: callers that
  do have a tier-1 record URL in scope should pass it via `click=` to
  avoid the tier-3 fallback. There is no enforcement of this; ADR-0036
  §Click URL priority is a convention, not a runtime check.
- The migration touches three import paths; one stale fork that
  imports `app.services.pushover` after this commit will fail at
  startup. Mitigation: the soak-pause holds the running container,
  so the cutover is a deliberate operator action.

### Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `NTFY_DRONEOPS_PUBLISHER_TOKEN` not set at cutover | Medium | High (alerts silently no-op) | Same fail-soft as the prior module — module logs a debug line ("ntfy skipped — not configured") and returns success; structured JSON logs continue. ADR-0036 plan Step 2 distributes the token before cutover. |
| ntfy on BOS-HQ blocks behind cloudflared/CF tunnel during incident | Low | Medium (fallback fires) | Fallback to ntfy.sh on the obscured topic, `[FALLBACK]` title prefix tells Bill's phone the in-band channel is degraded. |
| Dedup key prefix change causes spurious double-alerts during cutover | Negligible | Low | Prefix preserved (`doc:pushover:dedup:`) — in-flight dedup entries continue to suppress the exact same way after the rename. |
| Bearer token logged accidentally | Low | High | Token is read inside helpers and only embedded in the request `Authorization` header; never log-formatted, never included in error messages or response bodies. |

## Failover & Resilience Guard self-check

Per `CLAUDE.md` mandatory guard.

1. **Will this break PostgreSQL streaming replication?**
   No. No DB schema change, no port binding change, no `pg_hba.conf`
   change. The migration is application-layer outbound HTTP only.

2. **Will this survive a container recreation?**
   Yes. The new env var (`NTFY_DRONEOPS_PUBLISHER_TOKEN`) is wired in
   `docker-compose.yml` with the standard `${VAR:-}` defaulting
   pattern; container recreation rehydrates from env. The dedup key
   prefix is unchanged; in-flight Redis entries survive the rename.

3. **Will this break the blue-green swap flow?**
   No. The blue-green engine in `noc-master` does not reference this
   transport. Alerts during a swap fire through the helper as before.

4. **Will this break the failover engine?**
   No. The failover engine is in `noc-master` and uses its own ntfy
   helper (per ADR-0036 §Architecture). This repo's helper is
   independent — it is a consumer of notifications, not a participant
   in the failover decision. The ADR-0002 §5 watchdog continues to
   detect silent-drift; only the wire transport changes.

5. **Will this affect any customer-facing service during a site
   failover?**
   No. ntfy is operator-facing only. Customer flight-record uploads
   do not flow through this module.

## Implementation map

| Concern                             | File                                                                                |
|-------------------------------------|-------------------------------------------------------------------------------------|
| New ntfy module (replaces pushover) | `backend/app/services/ntfy.py`                                                      |
| Auth-failure call site              | `backend/app/auth/device.py:26` — `from app.services.ntfy import send_alert`        |
| Rotation FYI call site              | `backend/app/routers/admin_device_rotation.py:35` — `from app.services.ntfy import send_alert` |
| Silence watchdog call site          | `backend/app/tasks/celery_tasks.py` — `from app.services.ntfy import send_alert_sync` |
| Settings env var                    | `backend/app/config.py` — `ntfy_droneops_publisher_token: str = ""`                 |
| Compose env wiring                  | `docker-compose.yml` (3 service blocks: backend, worker, beat)                      |
| New tests                           | `backend/tests/test_ntfy.py` — 13 unit tests                                        |
| Existing tests                      | `backend/tests/test_device_key_rotation.py` — 2 mock renames (still 11 tests)       |

## References

- ntfy upstream: https://docs.ntfy.sh/publish/
- Fleet topic registry: `~/noc-master/data/ntfy-fallback-topics.yml`
  → `droneops: barnardhq-fleet-droneops-81b49d71de0f3e9fcf166e57f3c9846b`
- Notification standard: ADR-0036 §Notification standard
