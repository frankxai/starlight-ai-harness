#!/usr/bin/env python3
"""Starlight one-shot router: task description -> routing decision.

Combines route_classifier + route_executor. Prints the chosen model + fallback
chain. Use --record-success / --record-fail after the call completes to emit
the receipt (cost/latency supplied by caller).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from route_classifier import classify
from route_executor import route, record_receipt


def main():
    ap = argparse.ArgumentParser(description="Route a task to a model.")
    ap.add_argument("task", nargs="+", help="Task description")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", choices=["success", "fail"], help="Record a receipt")
    ap.add_argument("--used", help="Model actually used (required with --record)")
    ap.add_argument("--cost", type=float, default=None, help="Cost USD (required with --record)")
    ap.add_argument("--latency", type=int, default=None, help="Latency ms (required with --record)")
    ap.add_argument("--provider", default="", help="Resolved provider")
    args = ap.parse_args()

    task = " ".join(args.task)

    if args.record:
        if not args.used:
            print("error: --record requires --used", file=sys.stderr); sys.exit(2)
        if args.cost is None or args.latency is None:
            print("error: --record requires --cost and --latency", file=sys.stderr); sys.exit(2)
        try:
            c = classify(task)
            r = route(c["class"])
        except (FileNotFoundError, ValueError, KeyError) as e:
            print(f"error: {e}", file=sys.stderr); sys.exit(2)
        if args.used not in r["chain"]:
            print(f"error: --used '{args.used}' not in chain {r['chain']}", file=sys.stderr); sys.exit(2)
        try:
            fallback_used = (args.used != r["primary"])
            record_receipt(c["class"], args.used, fallback_used, args.cost, args.latency,
                          args.record == "success", args.provider)
        except (ValueError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr); sys.exit(2)
        print(f"receipt recorded: class={c['class']} used={args.used} success={args.record=='success'}")
        return

    try:
        c = classify(task)
        r = route(c["class"])
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)

    out = {
        "task": task,
        "class": c["class"],
        "source": c["source"],
        "primary": r.get("primary"),
        "variant": r.get("variant"),
        "fallbacks": r.get("fallbacks", []),
        "chain": r.get("chain", []),
        "cost_ceiling_usd": r.get("cost_ceiling_usd"),
        "zdr_required": r.get("zdr_required"),
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"class={out['class']} ({out['source']})")
        print(f"primary={out['primary']}  variant={out['variant']}")
        print(f"fallbacks={out['fallbacks']}")
        print(f"cost_ceiling=${out['cost_ceiling_usd']}  zdr={out['zdr_required']}")


if __name__ == "__main__":
    main()
