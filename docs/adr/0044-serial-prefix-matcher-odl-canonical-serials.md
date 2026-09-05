# ADR-0044: Canonical DJI serials in the fleet-attribution matcher

- **Status:** Accepted
- **Date:** 2026-09-05
- **Amends:** [ADR-0007](0007-strict-fleet-attribution-matcher.md) (strict fleet-attribution matcher). ADR-0007 remains in force; this ADR adds one narrowly-scoped rule inside its serial branch and changes nothing about its model branch.

## Context

### Two serial forms for the same airframe

A DJI airframe reports its serial number in two fixed-width forms, and
DroneOpsCommand ingests both:

| Form | Length | Example | Where it comes from |
|---|---|---|---|
| **DJI header form** | 16 | `1581F8HGX255P00A` | The DJI log file's own header. This is what `flight-parser` emits, and what 6 of the 7 original `aircraft` rows carry. |
| **OpenDroneLog form** | 20 | `1581F8HGX255P00A0FEK` | The 16-char header serial plus a 4-char ODL suffix. Every `source = 'opendronelog_import'` flight row carries this. |

The suffix is stable per airframe. Pairs verified against the production
database (BOS-HQ `droneops`, 2026-09-05):

| Model | 16-char header serial | ODL suffix |
|---|---|---|
| Matrice 30T | `1581F5BK7241J00B` | `A040` |
| Mavic 3 Pro | `1581F67QC23CN014` | `E4Z8` |
| Matrice 4TD | `1581F8HGX255P00A` | `0FEK` |
| Mini 5 Pro | `1581F9DEC257N029` | `3HSX` |
| Matrice 4T | `1581F7K3C25AA00D` | `MZMG` |
| Avata 2 | `1581F6W8A242N0A3` | `YVZ8` |
| M3P (DECOM) | `1581F67QE236L00A` | `0027` |

The two DJI FPV airframes (`37Q7LA800BX0PN`, `37QBJ5WBD100DN`) use a
**14-char** serial that carries no ODL suffix — byte-identical in both
forms.

### The defect

`_match_fleet_aircraft()` compared serials with exact equality only
(`func.upper(Aircraft.serial_number) == drone_serial.upper()`). A flight
whose stored serial is the 20-char form therefore never matched an
aircraft row carrying the 16-char form, even though both name the same
hardware.

Production state before this change (verified, not inferred):

```
SELECT drone_serial, length(drone_serial), drone_model, source, count(*)
  FROM flights WHERE aircraft_id IS NULL GROUP BY 1,2,3,4;

     drone_serial     | len | drone_model |       source        | count
----------------------+-----+-------------+---------------------+-------
 1581F8HGX255P00A0FEK |  20 |             | opendronelog_import |    49
 1581F7K3C25AA00DMZMG |  20 |             | opendronelog_import |    39
```

**88 flights** (49 Matrice 4TD + 39 Matrice 4T) unattributed purely
because of the form mismatch. The Matrice 4TD aircraft row has existed
since 2026-03-16 — this was never a missing-row problem. `drone_model`
is empty on all 88, so ADR-0007's model fallback could never have
rescued them either.

### The direction runs both ways

The `M3P - DECOM` fleet row was entered with the **20-char** form
(`1581F67QE236L00A0027`) while the DJI parser emits the **16-char** form
for that airframe. A rule that only handled "20-char flight → 16-char
aircraft" would leave that airframe's future uploads unattributed
forever.

## Decision

Add a second pass, **1b**, inside ADR-0007's serial branch. The serial
branch becomes:

1. **1a — exact match** (unchanged semantics). Case-insensitive equality
   against `Aircraft.serial_number`. **Exact equality always wins
   outright**; when it resolves, pass 1b never runs.
2. **1b — canonical match** (new). Reduce both the flight serial and each
   fleet serial to a canonical identity and require **full equality** of
   the canonicalized values.
3. If neither resolves, the flight stays unattributed. **No fall-through
   to model matching** — ADR-0007's central invariant, preserved
   verbatim.

The canonicalization is deliberately trivial:

```python
_DJI_SERIAL_LEN = 16
_ODL_SERIAL_LEN = 20

def _canonical_serial(serial: str) -> str:
    s = serial.strip().upper()
    return s[:_DJI_SERIAL_LEN] if len(s) == _ODL_SERIAL_LEN else s
```

It is a **fixed-width truncation to a fixed width**: only inputs of
*exactly* 20 characters are shortened, and only ever to *exactly* 16.
Every other length is returned unchanged.

### Why this is not the rule ADR-0007 banned

ADR-0007 removed **symmetric model-name prefix and substring matching**
(`fleet_norm.startswith(parsed_norm) OR parsed_norm.startswith(fleet_norm)`,
then `in` in either direction). Four properties separate that mechanism
from this one:

1. **Different risk class of identifier.** ADR-0007's prefixes were
   normalized *model names*: short, human-typed, drawn from a small
   marketing vocabulary where collisions are the norm, not the exception
   (`mavic3` ⊂ `mavic3pro` ⊂ `mavic3procine`). A serial is a **unique
   hardware identifier** issued per airframe. Two distinct airframes do
   not share a 16-char DJI serial; if they appear to, the fleet table has
   a data-entry error, which pass 1b reports as ambiguity rather than
   resolving.
2. **It is not a prefix test at all.** No `startswith`, no `LIKE`, no
   `in`. Both sides are reduced by a total function and then compared
   with `==`. A 12-char truncation canonicalizes to a 12-char string and
   can never equal a 16-char canonical; a 19- or 21-char string
   canonicalizes to itself. The only strings that can newly become equal
   are the 16-char and 20-char forms of one serial. That bound is what
   makes the widening auditable.
3. **It is not symmetric-open.** The rejected alternative — "either
   string starts with the other" — would let a truncated, partial, or
   hand-typed fragment (`1581`) match a real airframe. That is exactly
   the ADR-0007 failure shape and is explicitly rejected below.
4. **Exact still wins, and ties still refuse.** Pass 1b runs only after
   1a returns zero rows, and resolves only when it selects **exactly
   one** aircraft. Two or more → `None` plus an INFO log, per ADR-0007's
   posture.

### `scalar_one_or_none()` removed from the exact pass

The exact pass previously read `result.scalar_one_or_none()`, which
**raises** `MultipleResultsFound` when two rows match.
`aircraft.serial_number` has no unique index, so nothing in the schema
forbids duplicates. Widening the candidate set makes that a live crash
risk on every import path — and in the startup backfill an escaping
exception is caught by a blanket `except Exception` that logs
`"STARTUP: Aircraft backfill failed"` and abandons every remaining row.
Both passes now read `.scalars().all()` and treat "more than one" as
ambiguity, not as a crash. This does not depend on the fleet table
currently having zero duplicate serials.

### Aircraft rows with a blank serial are excluded

Production holds a `DJI Mavic 3 Pro` row with `serial_number IS NULL`.
Its canonical form is `''`. Pass 1b filters on
`(ac.serial_number or "").strip()` before comparing, so a blank fleet
serial can never participate as a candidate. (This is also why pass 1b
compares in Python rather than with SQL `LIKE`: a serial containing `%`
or `_` would otherwise act as a wildcard.)

### Logging

Every branch outcome logs at INFO under `doc.flights`, per ADR-0007 and
the repo's "Logging & Troubleshooting" standard. The new lines carry the
canonical form and the fleet row's stored serial, so an operator reading
logs alone can tell an exact match from a canonical one:

```
fleet-match: serial=… → aircraft=… (…) [exact]
fleet-match: serial=… → aircraft=… (…) [canonical serial …; fleet row carries …]
fleet-match: serial=… ambiguous — N fleet aircraft carry this exact serial; leaving unattributed
fleet-match: serial=… canonical=… ambiguous — N fleet aircraft share this canonical serial; leaving unattributed
fleet-match: serial=… (canonical=…) present but unmatched in fleet; leaving unattributed
```

## Consequences

**Positive:**
- The 88 unattributed `opendronelog_import` flights attribute correctly.
- Attribution stops depending on which ingest path recorded the serial.
- The `M3P - DECOM` airframe (fleet row in ODL form) can now match
  parser-emitted header-form serials.
- Duplicate fleet serials degrade to "unattributed + log line" instead of
  an exception that aborts a whole backfill run.

**Negative / accepted risk:**
- The candidate set for a serial-bearing flight is genuinely wider than
  before. If an operator types a 20-char serial into the fleet whose
  first 16 characters coincide with a *different* airframe's real serial,
  pass 1b will see two candidates and refuse — but if only the wrong row
  exists, it will match it. **This can misattribute.** It is a strictly
  smaller opening than the model-prefix rule ADR-0007 closed, not a
  closed one.
- The 4-char ODL suffix is treated as opaque. If OpenDroneLog ever emits
  a different suffix width, or a 20-char serial that is not
  header+suffix, the constants must be revisited.
- **This is a bulk write on deploy.** See below.

**Blast radius — the startup backfill.**
`_match_fleet_aircraft` is called by every flight-log upload path
(device, web, reprocess), by `POST /api/flight-library/backfill-aircraft`,
and by the startup auto-backfill in `backend/app/main.py` (~lines
438-458), which re-runs on every container restart. The moment this
deploys, that backfill will attribute **88 flights** — 49 to
`DJI Matrice 4TD` (`a140861c-…`) and 39 to `DJI Matrice 4T`
(`8accf428-…`) — and rewrite their empty `drone_model` to the fleet
`model_name`. That is the intended outcome.

Both backfill paths remain scoped to `Flight.aircraft_id IS NULL`; no
operator-curated assignment is re-evaluated or overwritten. That scoping
is an explicit invariant of ADR-0007 and is unchanged here.

Verification after deploy:

```sql
-- expect 0 rows
SELECT drone_serial, count(*) FROM flights
 WHERE aircraft_id IS NULL AND drone_serial IS NOT NULL AND drone_serial <> ''
 GROUP BY 1;

-- expect 49 / 39 on the two rows below
SELECT a.model_name, count(*) FROM flights f JOIN aircraft a ON a.id = f.aircraft_id
 WHERE f.source = 'opendronelog_import' AND f.drone_serial IN
   ('1581F8HGX255P00A0FEK','1581F7K3C25AA00DMZMG')
 GROUP BY 1;
```

and in the log: `STARTUP: Aircraft backfill — 88/88 unlinked matched`.

## Alternatives considered

- **Symmetric prefix — "either serial starts with the other."** Rejected.
  It is the exact shape ADR-0007 banned: a truncated or partial serial
  (`1581`, a half-pasted value, a hand-typed fragment) would match a real
  airframe, and the shorter the fragment the more airframes it reaches.
  The value of the chosen rule is precisely that it *cannot* do this.
- **Directional only: 20-char flight → 16-char aircraft.** Rejected on
  evidence. The `M3P - DECOM` fleet row carries the 20-char form, so the
  reverse direction is already live in production; a one-way rule would
  leave that airframe permanently unattributable.
- **SQL `LIKE aircraft.serial_number || '%'`.** Rejected. It is a true
  prefix test (unbounded), a `%` or `_` inside a serial becomes a
  wildcard, and a blank fleet serial matches everything.
- **Normalize the data instead: rewrite the 88 flight rows to the 16-char
  form.** Rejected. It fixes today's rows and nothing else — the next ODL
  import re-creates the problem, and it is a destructive edit to
  production data where a code rule suffices. (It also conflicts with
  FP-1's planned ODL-era re-import, which will land 20-char serials
  again.)
- **Store a normalized `serial_canonical` column on both tables.**
  Rejected for this change as disproportionate: a migration, a backfill,
  and two write paths to keep in sync, to replace a four-line pure
  function over an 11-row table. Worth revisiting if serial matching ever
  needs an index.
- **Match on the 4-char suffix table.** Rejected — it would require
  maintaining a per-airframe suffix registry that DJI does not publish
  and that is discovered only by observing imports.

## References

- Code: `backend/app/routers/flight_library.py` — `_canonical_serial`, `_match_fleet_aircraft`
- Code: `backend/app/main.py` — startup backfill (`Flight.aircraft_id.is_(None)`)
- Code: `backend/app/routers/flight_library.py` — `POST /backfill-aircraft`
- Tests: `backend/tests/test_flight_attribution.py` (22 cases; the ADR-0007 regression set is retained unchanged in intent)
- Amends: `docs/adr/0007-strict-fleet-attribution-matcher.md`
- Related: `docs/adr/0043-flight-details-sidecar-table-for-extended-log-data.md` (FP-1 will re-import ODL-era flights, which arrive with 20-char serials)
