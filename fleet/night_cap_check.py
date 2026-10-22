#!/usr/bin/env python3
"""Starlight night-cap gate: refuse unattended spend beyond the night cap.

Sums today's routing receipts (local day, America/Amsterdam) plus any extra
planned spend and fails (exit 2) when the total exceeds night_cap_usd from
routing.schema.json. Wire into night_runner manifest validation:

  python fleet/night_cap_check.py --extra-planned-usd 15
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "routing.schema.json"
RECEIPTS = HERE / "receipts" / "routing.jsonl"
LOCAL_TZ = timezone(timedelta(hours=2))  # CEST (winter CET = +1; Oct = +2)


def today_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(LOCAL_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


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
            ts = datetime.fromisoformat(r["ts"])
            # receipts are UTC; convert to local day window
            ts_local = ts.astimezone(LOCAL_TZ)
            if start <= ts_local < end and r.get("success"):
                total += float(r.get("cost_usd", 0))
        except (KeyError, ValueError, TypeError):
            continue
    return total


def main():
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
