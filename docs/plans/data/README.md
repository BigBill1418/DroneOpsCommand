# `docs/plans/data/` — committed data artifacts

Two files, both produced by the FP-1 log-recovery work
(`../2026-09-04-flight-details-data-ingestion.md` §8/§8a and
`../../reports/2026-09-05-fp1-log-recovery-hunt.md`). Neither is read by any code —
they are evidence, kept so the next session does not have to re-derive them.

Written 2026-09-21 because the TSV carries no header row and neither file's column
schema was documented anywhere a reader would find it.

| File | Rows | Header row? | Columns |
|---|---|---|---|
| `2026-09-04-drive-logs-inventory.csv` | 584 (+1 header) | **yes** | `filename, sha256, aircraft_name, product_type, aircraft_sn, battery_sn, start_time_utc, total_time_s, total_distance_m, max_height_m, max_hspeed_mps, app_platform, app_version, capture_num, already_on_bos` |
| `2026-09-05-missing-28-dji-originals.tsv` | 28 | **no** | tab-separated: `sha256`, `created_at` (date), `model`, `original_filename` |

**What they are.**

- **`2026-09-04-drive-logs-inventory.csv`** — the full header-parse inventory of the
  **584** OpenDroneLog-era DJI originals Bill shared from Google Drive ("Drone LOGS",
  2.34 GB, 2023-08-01 → 2026-03-17) and which were pulled to BOS-HQ
  `~/droneops-staging/drive-logs/` (outside the app volume, original filenames kept,
  covered by restic/R2 under tag `staging`). All 584 are log v14 and all parse with the
  current crate + production key. These are the source material for FP-1 **P7** (ODL
  re-import), which is not built.
- **`2026-09-05-missing-28-dji-originals.tsv`** — the manifest of the **28 `dji_txt`
  originals that are unrecoverable from any fleet source**. Their bytes only ever
  existed on HSH-HQ's `droneops_app_data` volume and were never inside the 2026-03-25
  migration tarball; HSH's backup script only ever ran `pg_dump`, so the files were
  never captured. The DB rows for these flights exist and are fully decoded — it is the
  **original files** that are gone, which is what blocks re-parsing them under a future
  parser. Two operator leads remain (`O-1`, `O-2` in
  `../../reports/2026-09-21-open-items-inventory.md`).

**Counts move.** The flight totals quoted in the plan and in ADR-0043 are
point-in-time; re-derive from the database rather than reading prose. These two files
are the exception — they are fixed manifests of a fixed historical set and do not
change.

**Scratch files that were never committed.** The recovery-hunt report names working
files (`missing_28_full.tsv`, `missing_28_hashes.txt`, `missing_28_filenames.txt`,
`db_dji_all.tsv`, `drive_sha256.txt`, `bos_192_hashes.txt`, `syno_appdata_24.txt`,
`migration_tar_24.txt`). None of those are in the repo; they lived on BOS-HQ during the
hunt. The two files in this directory are the only committed artifacts.
