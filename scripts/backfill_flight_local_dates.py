"""One-shot backfill: correct the date token baked into flight names (ADR-0017).

Before ADR-0017, ``_generate_flight_name`` stamped flight names with the
**UTC** date of ``start_time``: ``{label}_{YYYYMMDD}_{seq}``. For an evening
Pacific flight whose UTC instant rolls past midnight, that token was a day
ahead — e.g. a flight flown 2026-06-01 20:27 PDT was named
``DJI-Matrice-4TD_20260602_0001``.

The *displayed* date self-corrects once serialization + frontend format in the
operator timezone (no data change needed). This script fixes the only value
that is physically stored wrong: the ``YYYYMMDD`` token inside ``flights.name``.

Behaviour:
  - Source date = ``start_time`` if set, else ``created_at`` (mirrors name-gen).
  - Only rewrites names matching the auto-generated pattern
    ``^.+_\\d{8}_\\d{4}$``. Operator-customized names are left untouched.
  - The date token is recomputed in the operator timezone; the label and
    sequence number are preserved verbatim.
  - Collisions (target name already exists) are reported and skipped, never
    forced — re-run with the report to resolve by hand if any appear.
  - Idempotent and safe to re-run: already-correct names pass through.

DRY-RUN by default. Pass ``--apply`` to commit.

Run inside the backend container:
    docker compose exec backend python scripts/backfill_flight_local_dates.py          # preview
    docker compose exec backend python scripts/backfill_flight_local_dates.py --apply   # write
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

# Allow running from repo root: `python scripts/backfill_flight_local_dates.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.flight import Flight  # noqa: E402
from app.utils.timezone import local_date_compact  # noqa: E402

# Matches the auto-generated name shape: LABEL_YYYYMMDD_SEQ (seq is the 4-digit
# uniquifier at the very end). The label may itself contain digits/underscores,
# so anchor the date+seq to the end of the string.
_NAME_RE = re.compile(r"^(?P<label>.+)_(?P<date>\d{8})_(?P<seq>\d{4})$")


async def run(apply: bool) -> None:
    rewritten = 0
    unchanged = 0
    skipped_custom = 0
    collisions = 0

    async with async_session() as session:
        result = await session.execute(select(Flight))
        flights = result.scalars().all()
        existing_names = {f.name for f in flights}

        for f in flights:
            m = _NAME_RE.match(f.name or "")
            if not m:
                skipped_custom += 1
                continue

            source = f.start_time or f.created_at
            if source is None:
                skipped_custom += 1
                continue

            correct_token = local_date_compact(source)
            if m.group("date") == correct_token:
                unchanged += 1
                continue

            new_name = f"{m.group('label')}_{correct_token}_{m.group('seq')}"
            if new_name in existing_names:
                print(f"  COLLISION (skipped)  {f.name!r}  ->  {new_name!r}  (already exists)")
                collisions += 1
                continue

            print(f"  {f.name!r}  ->  {new_name!r}")
            if apply:
                existing_names.discard(f.name)
                existing_names.add(new_name)
                f.name = new_name
            rewritten += 1

        if apply:
            await session.commit()

    mode = "APPLIED" if apply else "DRY-RUN (no changes written; pass --apply to commit)"
    print(
        f"\n{mode}. rewritten={rewritten}  unchanged={unchanged}  "
        f"custom/skipped={skipped_custom}  collisions={collisions}  total={len(flights)}"
    )
    if collisions:
        print("NOTE: collisions were left untouched — resolve by hand if needed.")


if __name__ == "__main__":
    asyncio.run(run(apply="--apply" in sys.argv))
