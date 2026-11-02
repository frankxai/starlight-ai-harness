#!/usr/bin/env python3
"""Starlight night-cap gate: refuse unattended spend beyond the night cap.

Sums today's routing receipts (local day, America/Amsterdam) plus any extra
planned spend and fails (exit 2) when the total exceeds night_cap_usd from
routing.schema.json. Wire into night_runner manifest validation:

  python fleet/night_cap_check.py --extra-planned-usd 15
"""
import argparse
import calendar
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "routing.schema.json"
RECEIPTS = HERE / "receipts" / "routing.jsonl"


def _last_sunday(year: int, month: int) -> datetime:
    d = date(year, month, calendar.monthrange(year, month)[1])
    d -= timedelta(days=(d.weekday() + 1) % 7)
    return datetime(d.year, d.month, d.day)


def _in_dst(ts_utc: datetime) -> bool:
    """EU DST rule (Europe/Amsterdam, dep-free): summer UTC+2 from the last
    Sunday of March 01:00 UTC, winter UTC+1 otherwise. The shipped hard-coded
    +2 was wrong immediately after the 2026-10-25 switch and would silently
    flip direction every March and October."""
    start = _last_sunday(ts_utc.year, 3).replace(hour=1, tzinfo=timezone.utc)
    end = _last_sunday(ts_utc.year, 10).replace(hour=1, tzinfo=timezone.utc)
    return start <= ts_utc < end


def _local_day_bounds(day_utc: datetime) -> tuple[datetime, datetime]:
    """UTC pair bracketing the local calendar day containing `day_utc`.
    The window is exactly 24h; the only skew is a 1-hour edge on the two
    days a year that contain the actual clock switch - acceptable for a
    spend gate."""
    off = 2 if _in_dst(day_utc) else 1
    local = day_utc.astimezone(timezone.utc) + timedelta(hours=off)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local - timedelta(hours=off)
    return start, start + timedelta(days=1)


def today_bounds() -> tuple[datetime, datetime]:
    return _local_day_bounds(datetime.now(timezone.utc))


def spent_today() -> float:
    if not RECEIPTS.exists():
        return 0.0
    start, end = today_bounds()
    total = 0.0
    for line in RECEIPTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            ts = datetime.fromisoformat(r["ts"]).astimezone(timezone.utc)
            if start <= ts < end and r.get("success"):
                total += float(r.get("cost_usd", 0))
        except (KeyError, ValueError, TypeError):
            continue
    return total


def selftest() -> int:
    """Boundary table: UTC instant -> Amsterdam local date under the EU rule.
    The first winter pair is the actual 2026-11-02 catch: 22:30Z is still
    Nov 1 in Amsterdam under CET (+1) but the old +2 code called it Nov 2."""
    cases = [
        ("2026-06-15T22:30:00", "2026-06-16"),  # summer (+2): 00:30 next local day
        ("2026-11-01T22:30:00", "2026-11-01"),  # winter (+1): SAME local day
        ("2026-11-01T23:59:00", "2026-11-02"),  # one minute before the true boundary
        ("2026-10-24T23:30:00", "2026-10-25"),  # still CEST (+2) before the switch
        ("2026-10-25T01:30:00", "2026-10-25"),  # 10:30Z+1 already, switch day
    ]
    ok = True
    for iso_z, want in cases:
        ts = datetime.fromisoformat(iso_z + "+00:00")
        off = 2 if _in_dst(ts) else 1
        got = (ts + timedelta(hours=off)).date().isoformat()
        start, end = _local_day_bounds(ts)
        len_ok = (end - start) == timedelta(hours=24)
        if got != want or not len_ok:
            print(f"  FAIL {iso_z}: local {got} want {want} window24h={len_ok}")
            ok = False
    if not ok:
        return 1
    print("night cap selftest OK: 5 DST/day-boundary cases, windows exactly 24h")
    return 0


def main():
    if sys.argv[1:2] == ["selftest"]:
        raise SystemExit(selftest())
    ap = argparse.ArgumentParser(description="Night-cap spend gate.")
    ap.add_argument("--extra-planned-usd", type=float, default=0.0,
                    help="Additional planned spend this night (manifest estimate)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    cap = float(schema["night_cap_usd"])
    spent = spent_today()
    total = spent + args.extra_planned_usd
    ok = total <= cap
    out = {
        "cap_usd": cap,
        "spent_today_usd": round(spent, 4),
        "extra_planned_usd": args.extra_planned_usd,
        "total_usd": round(total, 4),
        "ok": ok,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        status = "OK" if ok else "BLOCK"
        print(f"[{status}] night cap ${cap}: spent ${out['spent_today_usd']} "
              f"+ planned ${args.extra_planned_usd} = ${out['total_usd']}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
