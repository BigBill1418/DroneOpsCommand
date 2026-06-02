# ADR-0018 — Deploy path is the NOC fleet deployer; per-repo autopull is retired

- **Status:** Accepted
- **Date:** 2026-06-02
- **Supersedes:** the per-repo autopull deploy mechanism (see commit `e4610b5`)
- **Related:** ADR-0017 (timezone fix — the change that exposed the stale-deploy), NOC-Master-Control ADR-0056 (fleet deploy gate), NOC-Master-Control ADR-0079 (digest gate observes `build:`-only services)

## Context

DroneOpsCommand once shipped its own per-repo continuous-deploy mechanism:

- `update.sh` — the actual build/restart script.
- `autopull.sh` — a poller that `git fetch`ed origin/main and, on new
  commits, invoked `bash "$DIR/update.sh"`.
- `droneops-autopull.timer` + `droneops-autopull.service` — systemd units
  that ran `autopull.sh` every ~3 minutes.
- `setup-server.sh` — installed and enabled all of the above.

On **2026-04-01** commit `e4610b5` ("Remove per-repo deployer — now managed
by NOC Master Control auto-deployer") deleted `update.sh`, because the
fleet had standardized on a single deploy authority: the **NOC Master
Control fleet deployer** (`swarmpilot_deployer`, a Docker Swarm service on
HSH-HQ) polls every enrolled repo's `origin/main`, syncs the production
host's working tree, rebuilds the changed services, and recreates the
containers. DroneOpsCommand production runs on **BOS-HQ**; the deployer
SSHes there to build + `docker compose up -d`.

But `e4610b5` left the rest of the autopull scaffolding behind. The result
was a repo that *advertised two deploy paths where one was a corpse*:

- `autopull.sh` still called the deleted `update.sh` (would fail at the
  deploy step if ever run).
- `droneops-autopull.timer` / `.service` referenced `autopull.sh` (the
  units were `disable`d/`inactive` on every host, but present on disk).
- `setup-server.sh` still installed + enabled the autopull timer and ran
  `chmod +x update.sh` on the non-existent file.

These dead artifacts actively misled operators (they cost a wrong turn
during the 2026-06-02 stale-deploy investigation, sending the reader toward
a non-functional per-repo poller instead of the real fleet-deployer path).

Separately, the 2026-06-02 investigation found the *real* reason a push had
not rebuilt the containers: DroneOpsCommand's compose services have
`build:` but no `image:` name, which blinded the fleet deployer's
image-digest gate. That is fixed in **NOC-Master-Control ADR-0079** (the
deployer now resolves `build:`-only services to their compose default image
`<project>-<service>:latest`). This ADR records the *repo-side* half: the
deploy path and the disposition of the orphaned autopull scaffolding.

## Decision

**The sole deploy path for DroneOpsCommand is the NOC fleet deployer.**
There is no per-repo deploy mechanism, and none should be reintroduced
(re-adding one would violate the fleet single-deployer discipline and risk
double-deploys / split-brain).

The orphaned per-repo autopull artifacts are **removed entirely**:

- Deleted `autopull.sh`, `droneops-autopull.timer`, `droneops-autopull.service`
  (the `update.sh` they depended on has been gone since `e4610b5`).
- Deleted the stale tracked `autopull.log` and its now-dead `.gitignore`
  entries (`autopull.log`, `.autopull.lock`, `.last_deployed_commit`).
- Reconciled `setup-server.sh`: it no longer installs/enables the autopull
  timer and no longer `chmod`s the deleted `update.sh`. It now installs
  **only** `droneops.service` (the boot-time stack-start unit, which merely
  runs `docker compose up -d` on boot — it does NOT deploy/pull). Its
  `--uninstall` path also belt-and-suspenders-removes any legacy autopull
  units a prior install may have left on a host. A loud banner states that
  deploys are the fleet deployer's job.

`droneops.service` is **kept** because it is orthogonal to deployment: it
ensures the stack comes back up after a host reboot. It does not fetch,
build, or pull — so it does not constitute a second deploy path.

## Consequences

- The repo no longer advertises a dead second deploy path; the only
  deploy mechanism documented and present is the NOC fleet deployer.
- Operators land on the correct path: pushes to `main` are deployed by
  `swarmpilot_deployer`; deploy history is at
  `https://noc-mastercontrol.barnardhq.com/deploys`.
- `setup-server.sh` is safe to run on a fresh production host — it installs
  only the boot-start unit and will not resurrect the retired poller.
- No production behavior change: the autopull units were already inactive
  on every host; this removes the misleading on-disk corpses.
