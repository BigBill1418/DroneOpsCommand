# FP-1 forensic log-recovery hunt — round 2 (2026-09-05, read-only + one backup-hygiene fix)

Follow-up to `docs/plans/2026-09-04-flight-details-data-ingestion.md` §8 / §8a.
All times Pacific unless labelled UTC. Host clocks are `America/Los_Angeles`;
container logs and R2/restic timestamps are UTC.

## 1. Verdict on the 28 missing `dji_txt` originals

**Definitively unrecoverable from any fleet source.** The remaining hope
(NEXTL3VEL + its Active Backup image repo) is *narrower and more time-critical*
than §8 assumed, and the evidence says those files almost certainly never
touched that PC.

### The loss mechanism, now proven rather than inferred

- 52 `dji_txt` rows were created 2026-03-23 → 2026-04-19 (the HSH-HQ prod era).
- Exactly **24** of those 52 have a file on BOS today — and that set of 24 is
  **byte-identical to the 24 files inside `~/migration/doc_appdata.tar.gz`**
  (the 2026-03-25 Synology→HSH migration snapshot), which was replayed onto
  BOS's fresh volume on 2026-04-20. Their DB `created_at` values are
  2026-03-23 (1), 2026-03-24 (1), 2026-03-25 (22).
- The other **28** only ever existed on HSH's `droneops_app_data` volume.

So the survival boundary is not a date — it is *"was this file inside the
migration tarball?"*. Anything uploaded to HSH after that tarball was cut is
gone.

### Why no backup can hold them (each closed by a run command, not by assumption)

| Lane | Evidence it cannot contain the 28 |
|---|---|
| HSH `droneops_app_data` volume | `docker volume ls` on droneops-server → 11 volumes, **none** droneops. Gone. |
| **HSH nightly backup (the decisive one)** | `~/backups/pg-backup.sh` is still on disk. It runs `pg_dump` on `droneops-db-1` and an n8n sqlite `.backup` — **and nothing else**. It never touched `/data/uploads` or the app-data volume. The HSH prod era had *no file-level backup of flight logs at all*. `~/backups/README.RETIRED.md` + `pg-backup.log` (last entry 2026-04-15T02:00:26Z) corroborate. |
| restic repo `droneops-backups` (R2) | 48 snapshots, **oldest 2026-08-17T06:24:29Z** — four months after the loss. Union of `/data/uploads/flight_logs` across all 15 `files` snapshots = **184 hashes**, of which **0** are in the 28 and **0** are absent from live disk. |
| Legacy plaintext mirror `s3://obs-glitchtip-backups/droneops/uploads/flight_logs/` | **184** hashes (§8 said 183 — now 184), **earliest object 2026-07-16**. 0 of the 28; 0 keys not on live disk. Whole-bucket sweep (701 keys): no `FlightRecord` keys, no 64-hex `.txt` outside that prefix. |
| HSH `~/migration/` tarballs | `doc_appdata.tar.gz` → 24 flight-log entries, **0** of the 28, all 24 on BOS. Other HSH tarballs (`vol-archive-20260727/*`, `agile0-gitea.tar.gz`): 0 flight-log entries. |
| Synology docker volume `droneops_app_data` | 24 hashes — the *same* 24 as the tarball. 0 of the 28. |
| Synology Hyper Backup / snapshot lanes | `@sharesnap` holds real snapshots for **"Important Docs" only** (every other share's `.meta` is 0 bytes). `@S2S`, `@C2Share`, `@cloudsync`, `@img_bkp_cache`, `@Repository`, `@download`: 0 FlightRecord-named files, 0 64-hex `.txt`. |
| Synology `UNAS Backup` share | **Empty** (created 2026-03-28, only an `@eaDir` stub). Never populated. |

### Content-shape sweeps (by name, not folder) — all negative

Searched for `DJIFlightRecord*`, `FlightRecord_*`, `FLY*.DAT`, and
`*/[0-9a-f]{64}.txt`:

- **HSH-HQ (droneops-server)** — full `/` sweep. Only hits: DJI test fixtures in
  *another agent's* workspace scratchpad (`titanforge-workspaces-4/.../logs2`,
  `out2`, `logs`) and `/tmp/pytest-of-bbarnard065/...` unit-test temp dirs. No archive.
- **CHAD-HQ (svdp-dev)**, **BOS-HQ**, **Oracle (ocean)**, **SDR Pi**, **Dev-Ops-2** —
  `/home /var/lib/docker /opt /srv /root`: **zero** hits on every host.
  (BOS excludes `~/droneops-staging`, which is the known 584.)
- **Synology RS1221+ (HSH-RS, 192.168.50.20)** — every user share plus the
  privileged `@docker` tree. Only hits fleet-wide are, in `/volume1/Downloads`:
  `DJIFlightRecord_2024-12-18_17-36-34.kml`,
  `FlightRecord_2026-03-08_16-36-59.gpx`, `.json`, and
  `OpenDroneLog_Signature_576flights.png` — exports, not logs.
  One false positive: `/volume1/SVDP Backup/.../Endicia/DAZzle/Flyer.dat`
  (postage software, matched `FLY*.DAT`).
- **The 584 recovered Drive logs** — 0 hash overlap with the 28 (confirmed
  directly, not inherited from §8). Newest staging filename is 2026-03-11.

### The 28, with the search key §8 never recorded

§8 recorded only counts and airframes. The DB also carries
`flights.original_filename` for all 28 — **this is what a portal/Explorer search
must be run against.** Two distinct prefixes are in play, so searching only
`DJIFlightRecord` misses 12 of the 28:

- `DJIFlightRecord_YYYY-MM-DD__HH-MM-SS_.txt` — 16 files (M4TD, M30T; DJI Pilot 2)
- `FlightRecord_YYYY-MM-DD__HH-MM-SS_.txt` — 9 files (Mavic 3 Pro; DJI Fly)
- `FlightRecord_YYYY-MM-DD_[HH-MM-SS].txt` — 3 files (Mini 5 Pro; bracketed form)

Flight dates span 2026-03-22 → 2026-04-19. Full hash + date + model + filename
manifest: `missing_28_full.tsv` (alongside this file).

## 2. NEXTL3VEL and the Active Backup lead — what actually changed

### The PC is still offline (verified, not assumed)

From HSH: `ping 192.168.50.74` → **`From 10.50.0.1 icmp_seq=1 Destination Host
Unreachable`** ×3 (the gateway cannot ARP it — this is "powered off", not a
firewall drop). Corroborated by TCP probes on 445/3389/135/139/22/5985, all
refused. The Synology at 192.168.50.20 answers fine on the same subnet, so the
path is good.

### §8 is wrong about the Active Backup version range — and the window is closing

§8 says "versions 2026-05-29 → 2026-09-01". The repo holds **exactly 10
versions, 2026-05-29 04:30 → 2026-06-07 04:30**, and nothing since:

```
ls /volume1/ActiveBackupforBusiness/ActiveBackupData/PC-NEXTL3VEL-bbarnard065-Default/
  → ActiveBackup_2026-05-29_043001 ... ActiveBackup_2026-06-07_043000   (10 dirs)
```

ABB's activity log explains it — last success then daily failure ever since:

```
2026-06-07T04:37:06-07:00 [bbarnard065-Default] The backup task ... was completed.
2026-06-08T00:00:20-07:00 [bbarnard065-Default] Successfully deleted version [2026-05-28 03:30:07]
2026-08-11..2026-09-05    [bbarnard065-Default] Device NEXTL3VEL missed scheduled backups.
```

The "Sep 1" in §8 is the *parent directory's* mtime (the nightly retention pass
touching it), not a version.

**Time-critical consequence:** the task retains **10 versions** (proven — the
11th nightly on 2026-06-08 deleted the 2026-05-28 version). The 2026-05-29 →
2026-06-07 images are the only historical pictures of that PC. **If NEXTL3VEL is
powered back on and the task resumes, ten successful nights will rotate every
one of them out.** The portal search must happen *before* the PC rejoins the
backup task, or the task must be paused first.

### What is enumerable from the shell — the honest answer: nothing useful

I read the repo without mounting or restoring anything. Each version directory
contains only:

```
0.img.delta          (block delta)
backup_db.sqlite     (12.4 MB)
device_spec          (JSON)
```

`backup_db.sqlite` has five tables and **no file catalog**:
`config_table` (15 rows), `interval_table` (425,244 rows — block extents),
`mac_info_table` (0), `performance_table` (164), `progress_table` (1).
`@ActiveBackup/@data` holds the 1.1 TB dedup chunk store (`Pool/`,
`sample.index`, `file_map.db`) — content-addressed, no filenames.
`/var/packages/ActiveBackup/target/indexdb/` contains only `appindexdb` and
`helpindexdb` (5.2 MB total) — the DSM app/help search index, not a backup file
index.

**Conclusion: file-level search is only possible through the Active Backup
portal, which mounts the NTFS image on demand. There is no shell path, and I did
not attempt a restore.**

One useful thing the shell *does* tell us: `device_spec` for all 10 versions
shows **disk0 only, 6 volumes, one NTFS `C:\`**. If Bill keeps drone data on a
second physical drive, **it is not in the image at all.**

### Exact operator steps for Bill

DSM → **Active Backup for Business** → **Restore** → task **`bbarnard065-Default`**
(task_id 7, uuid `312203a5-6fa1-489b-aa4c-1f4b91af5487`) → **Restore
files/folders**. DSM on that Synology is LAN-only from where the fleet sits, so
reach it however he normally does remotely (DDNS/QuickConnect is enabled on the
box).

1. Pick version **`2026-06-07 04:30`** first (most recent), then work backwards
   through `2026-06-01`, `2026-05-29` if empty.
2. Search these strings **separately** — one prefix does not cover the other:
   - `DJIFlightRecord_`
   - `FlightRecord_`
   Restrict by extension `.txt`. Ignore `.kml` / `.gpx` / `.json` hits (exports).
3. Date range to care about: file names containing **`2026-03-22`** through
   **`2026-04-19`**.
4. Folders worth browsing directly if search comes back empty:
   `C:\Users\bbarnard065\Desktop`, `\Downloads`, `\Documents`,
   `C:\Users\bbarnard065\OneDrive\{Desktop,Documents}`, the Google Drive local
   mirror, and `C:\$Recycle.Bin`.
5. If anything is found: copy it to a fleet host and sha256-compare against
   `missing_28_hashes.txt` before treating it as a recovery.

**Do this before reconnecting the PC to the backup task** (see the retention
warning above).

### Honest prior: this lead is weak, and here is why

DroneOpsSync — which §8 and a code comment in
`droneops/backend/tests/test_flight_ingest_consolidated.py:11` both describe as
"the Windows companion" — **is not a Windows app and never was.** It is a Kotlin
Android APK (`applicationId "com.droneopssync.app"`) sideloaded onto the DJI
controller. A Windows companion was formally proposed and **rejected** in
DroneOpsSync ADR-0007 (2026-05-16, "No-go on both option 1 and option 2").
The field build across the entire loss window was v1.3.23 (2026-03-29), which
read `/sdcard` in place and uploaded straight to the backend over the network.
**NEXTL3VEL was never in the ingest path**, so a copy would only exist there if
Bill separately hand-pulled those flights over MTP for some other reason.

Two independent archives of that PC's Downloads folder sit on the Synology and
are readable right now — `/volume1/Downloads/5.7.26 From N3XT Level` (1,485
entries, 2026-05-07) and `/volume1/Downloads/8.12.26 - Home DL Folder Archive`
(1,766 entries, 2026-08-12). **Neither contains a single raw `.txt` flight log**
— only the 2024 `.kml` and the 2026-03-08 `.gpx`/`.json` exports. That is direct
evidence against the most likely hiding place on that machine, at two dates
straddling the image window.

### Phone / RC FlightRecord folders — operator-only, realistically

`DEFAULT_PATHS` in DroneOpsSync (`MainViewModel.kt`) are:
`/storage/emulated/0/Android/data/dji.go.v5/files/FlightRecord`,
`/storage/emulated/0/DJI/com.dji.industry.pilot/FlightRecord`,
`/storage/emulated/0/Android/data/com.dji.fly/files/FlightRecord`,
`/storage/emulated/0/DJI/dji.go.v4/FlightRecord`.

v1.3.23 deleted the controller original on an operator-confirmed dialog (never
automatically). **If Bill ever declined or skipped that dialog, the originals
are still on the controller.** That is the single highest-probability surviving
copy of the 28. Steps: power up the M4TD / M30T controller and the Mavic 3 Pro
device, open DroneOpsSync (or Windows Explorer over MTP), browse the paths above,
and look for names in the 2026-03-22 → 2026-04-19 range. Note ADR-0006: on the
RC Pro 2 the SAF grant lacked WRITE before v1.3.28, so deletes were *silently
failing* — which makes surviving controller copies from that era more likely,
not less. Android 13+/OneUI 7 blocks the phone MTP path, so the phone-side
Mavic/Mini logs are the harder half.

Partial-recovery note: the aircraft's own `FLY###.DAT` logs (pullable via DJI
Assistant 2) cover the same flights but are a **different byte stream** — they
can never satisfy a sha256 match against the 28, so under the plan's checksum
invariant they would be new data, not a recovery of these rows.

## 3. Gap 2 — `~/droneops-staging` backup hygiene: FIXED

**Risk confirmed before acting**, not assumed: `grep -rn droneops-staging` across
`~/*.sh`, `*.env`, `*.service`, `*.yml` on BOS returns exactly one hit — the
staging dir's own `run_full.sh`. It was in **no** backup lane. 584 files,
2.2 GB, single copy, on one host.

**What I did (reversible, non-destructive, nothing moved or deleted):** one
restic snapshot of `~/droneops-staging` into the **existing** `droneops-backups`
R2 repo under a new tag `staging`, mounting the source **read-only**. No script
edit, no config change — so nothing for the deployer to clobber, and no
divergence between the BOS clone and git.

```
snapshot 4c08afa7  2026-09-05 08:23:17 UTC (2026-09-05 01:23 PDT)
  host BarnardHQ-BOS  tag staging  path /staging  2.181 GiB
  593 files new, added 2.133 GiB (2.129 GiB stored)
repo raw-data: 825.259 MiB → 2.934 GiB
```

**Verified by observing the end state, not the exit code:**

1. `restic ls 4c08afa7 /staging/drive-logs` → 585 entries (584 files + the dir);
   `comm` against the live directory → **0 missing**.
2. Four files pulled back **down from R2** via `restic dump` and sha256'd
   against `~/droneops-staging/drive_sha256.txt` — all four byte-identical:
   ```
   OK 9ab0bd84…  DJIFlightRecord_2024-07-19_[20-40-06].txt
   OK 417b8927…  DJIFlightRecord_2026-02-28_[18-35-34].txt
   OK 4c99c9ff…  DJIFlightRecord_2023-12-09_[10-54-40].txt
   OK 6a881eaa…  DJIFlightRecord_2025-12-14_[15-29-11].txt
   ```
3. **Retention proven, not assumed.** `restic forget --dry-run` with the exact
   production policy (`--keep-daily 14 --keep-weekly 8 --keep-monthly 24
   --keep-yearly unlimited --group-by tags`) explicitly keeps it:
   ```
   keep 1 snapshots:
   4c08afa7  2026-09-05 08:23:17  BarnardHQ-BOS  staging  daily snapshot  /staging  2.181 GiB
   ```
   Zero removals across every tag group. This matches the three existing
   singleton `legacy*` tags that have survived ~19 days of nightly
   `forget --prune`.

**Cost:** ~2.13 GiB extra in R2 (~$0.03/month). DJI logs are near-incompressible
— repo compression ratio fell 1.85× → 1.23×, as expected.

**Residual risk, stated plainly:** this is a **one-shot** snapshot of a static
archive, not a recurring lane. If files are ever *added* to
`~/droneops-staging`, they are unprotected until someone re-runs it. The durable
fix is still the §8a plan: let P7 ingest into `/data/uploads/flight_logs/`
(covered by the `files` lane), or land a `staging` lane in
`scripts/droneops-backup.sh` through git. I deliberately did not edit that
script — the BOS copy is the deployer's clone and an out-of-band edit would be
reverted on the next deploy.

## 4. What in §8 / §8a is stale or wrong

1. **Counts have moved.** `dji_txt` rows **210 → 218**; files on disk **184 →
   192**. Eight new logs were uploaded 2026-09-04 23:47–23:49 PDT. The **28
   missing is unchanged** — none of the new uploads touch the gap. Every
   "184 files / 182 usable" figure now reads **192 / 190**; "210 retained
   originals reads as 182" now reads **218 → 190**.
2. **ABB version range is wrong and materially so.** Not "2026-05-29 →
   2026-09-01" — it is **2026-05-29 → 2026-06-07, 10 versions**, with the task
   failing nightly since 2026-06-08. Plus the 10-version retention means the
   evidence window is destroyed by simply turning the PC back on.
3. **"DroneOpsSync deletes the controller copy after a confirmed sync"** is
   accurate about the *controller* but has been read as implying a PC-side path.
   DroneOpsSync is an **Android APK on the controller**; there is no Windows
   component (DroneOpsSync ADR-0007 rejected one). The comment at
   `droneops/backend/tests/test_flight_ingest_consolidated.py:11` calling it
   "the Windows companion" is **wrong** and is likely the source of the
   confusion — worth fixing. Also: the delete requires an operator tap, and
   before v1.3.28 it was *silently failing* on the RC Pro 2 (ADR-0006) — which
   is why the controller is now the best remaining lead, not a dead one.
4. **"Backups only began 2026-07-16"** is right in spirit but imprecise, and the
   precise version is stronger: the HSH-era nightly (`~/backups/pg-backup.sh`)
   *did* run through 2026-04-15 — it was **DB-only by design** and never
   captured files. That is a firmer closure than "backups didn't exist yet".
5. **Legacy S3 mirror is 184, not 183.**
6. **`~/backups/postgres/` on HSH no longer exists** — swept 2026-08-17 into the
   restic `legacy` tag (`README.RETIRED.md`). §8 should not send a future
   session looking for it. Surviving artifacts: restic snapshots `5748b3e8`
   (`legacy`, `droneops_20260415_020001.dump`), `1d0cfb76` (`legacy-n8n`),
   `66ed2135` (`legacy-bos-primary-pgdata`).
7. **New artefact §8a should know about (P7-relevant):** an OpenDroneLog DuckDB
   backup **`2026-03-06_20-38-33_Open_Dronelog.db.backup`** (95,605,368 bytes,
   md5 `56a156aafffd6efdeb2cd51dec2063af`) exists in **both** Synology Downloads
   archives, alongside `OpenDroneLog_Signature_576flights.png`. §8a says the
   surviving ODL DuckDB was last written **2026-02-28** and that the 36 later
   flights live in an instance "whose volume is gone". This copy is **one week
   newer** and its companion PNG says **576 flights** — it may well carry the
   sha256s for some of the 36 that currently match by filename only. Worth
   opening before P7 runs. (The `drone-data` docker volume on the Synology holds
   only a 20 KB `flights.db` + a `keychains` dir — a different, near-empty
   instance.)
8. **The 28's `original_filename` values were never recorded in the plan.** They
   are the only practical search key for any future hunt. Captured in
   `missing_28_full.tsv`.

## 5. Everything searched, with the command

All commands run read-only unless noted.

| Target | Command | Result |
|---|---|---|
| NEXTL3VEL reachability | `ping -c3 192.168.50.74`; `nc -z -w3 … 445/3389/135/139/22/5985` | **OFFLINE** — gateway `Destination Host Unreachable`; all 6 ports refused |
| LAN sanity control | `ping 192.168.50.20` / `192.168.50.1` | both up (0% loss) |
| Prod DB truth | `docker exec droneops-standby-db psql -c "SELECT source,count(*) FROM flights GROUP BY source"` | `opendronelog_import` 584, `dji_txt` 218 |
| DB vs disk | hash-set `comm` of 218 DB hashes vs 192 files | 28 missing, 2 file-without-DB (40 B / 70 B dummies) |
| BOS live store | `docker exec droneops-backend-1 ls /data/uploads/flight_logs \| wc -l` | 192 |
| restic snapshot census | `restic snapshots --json` | 48 total: 15 `db`, 15 `files`, 15 `config`, 3 `legacy*`; oldest **2026-08-17T06:24:29Z** |
| restic union of logs | `restic ls <id>` × all 15 `files` snapshots | union **184**; 0 of the 28; 0 absent from disk |
| Legacy S3 mirror | `aws s3 ls s3://obs-glitchtip-backups/droneops/uploads/ --recursive` | 210 keys, **184** log hashes, earliest **2026-07-16**; 0 of the 28 |
| Whole obs bucket | `aws s3 ls s3://…/ --recursive` (701 keys) | 0 `FlightRecord` keys; 0 64-hex `.txt` outside the known prefix |
| HSH migration tarball | `tar tzf ~/migration/doc_appdata.tar.gz \| grep flight_log` | 24 files; **0** of the 28; all 24 on BOS |
| Other HSH tarballs | `tar tzf` × `vol-archive-20260727/*`, `agile0-gitea.tar.gz` | 0 |
| HSH surviving volumes | `docker volume ls` | 11 volumes, **no droneops volume** |
| HSH retired backup script | `cat ~/backups/pg-backup.sh` | **pg_dump + n8n sqlite only — no file lane** |
| HSH full filesystem | `find / -xdev` for FlightRecord / FLY*.DAT / 64-hex `.txt` | only other-workspace test fixtures + pytest tmp |
| CHAD / BOS / Oracle / SDR / Dev-Ops-2 | `find /home /var/lib/docker /opt /srv /root -xdev …` | **0** on every host |
| Synology user shares | `find` over Downloads, UNAS Backup, Important Docs, homes, docker, @sharesnap, UTS Work Data Archive, photo, video, web, /volume2 | only `.kml`/`.gpx`/`.json` exports in Downloads |
| Synology privileged shares | `sudo find` over Call Archive, Nexar DC Data, SVDP Backup, VM Image Storage, web_packages, @appdata, @S2S, @cloudsync, @C2Share, @img_bkp_cache, @Repository, @download | 0 real hits (1 false positive: `Endicia/DAZzle/Flyer.dat`) |
| Synology docker volumes | `sudo find /volume1/@docker/volumes/droneops_app_data` | 24 hashes = the tarball 24; 0 of the 28 |
| Synology `UNAS Backup` | `ls -la` / `find` | **empty** — only an `@eaDir` stub |
| Synology snapshot lane | `ls /volume1/@sharesnap/` | real snapshots for "Important Docs" only |
| ABB version list | `ls .../PC-NEXTL3VEL-bbarnard065-Default/` | **10 versions, 2026-05-29 → 2026-06-07** |
| ABB per-version catalog | `sqlite3 backup_db.sqlite .schema` | 5 tables, **no file index** |
| ABB volume coverage | `device_spec` × all 10 versions | disk0 only, 6 volumes, one NTFS `C:\` |
| ABB failure history | `sudo grep "NEXTL3VEL missed" activity.log` | last success **2026-06-07 04:37 PDT**; daily misses since |
| PC Downloads archives | `ls`/`find` over the 2026-05-07 and 2026-08-12 Synology copies | 1,485 / 1,766 entries — **no raw `.txt` flight logs in either** |
| 584 Drive set vs the 28 | `comm` of `drive_sha256.txt` vs `missing_28_hashes.txt` | **0** overlap (20 overlap with BOS, as §8a says) |

## 6. Artifacts (scratchpad, alongside this file)

- `missing_28_full.tsv` — hash / created_at / model / **original_filename** for all 28
- `missing_28_hashes.txt`, `missing_28_filenames.txt`
- `bos_192_hashes.txt` — current live store
- `db_dji_all.tsv` — all 218 `dji_txt` rows
- `migration_tar_24.txt`, `syno_appdata_24.txt` — the 24 that survived, both sources
