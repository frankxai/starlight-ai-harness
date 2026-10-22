#!/usr/bin/env python3
"""Weekly routing review: parse receipts + token-tracker reports, propose routing
diffs. Writes fleet/routing-proposals/YYYY-MM-DD.json for human approval.
Never auto-commits (money/merges stay on the human's button).
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPTS = HERE / "receipts" / "routing.jsonl"
PROPOSALS = HERE / "routing-proposals"
SCHEMA = HERE / "routing.schema.json"
MIN_SAMPLES = 20


def load_receipts(days: int = 7) -> list[dict]:
    if not RECEIPTS.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out = []
    for line in RECEIPTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            out.append(r)
    return out


def analyze(receipts: list[dict]) -> dict:
    by_class = defaultdict(lambda: {"n": 0, "success": 0, "cost": 0.0, "latency": 0, "models": defaultdict(lambda: {"n": 0, "success": 0, "cost": 0.0})})
    for r in receipts:
        c = by_class[r["task_class"]]
        c["n"] += 1
        c["success"] += int(r["success"])
        c["cost"] += r["cost_usd"]
        c["latency"] += r["latency_ms"]
        m = c["models"][r["used"]]
        m["n"] += 1
        m["success"] += int(r["success"])
        m["cost"] += r["cost_usd"]

    result = {}
    for cls, d in by_class.items():
        sr = (d["success"] / d["n"]) if d["n"] else 0.0
        avg_cost = (d["cost"] / d["n"]) if d["n"] else 0.0
        avg_latency = (d["latency"] / d["n"]) if d["n"] else 0.0
        models = {}
        for m, md in d["models"].items():
            models[m] = {
                "n": md["n"],
                "success_rate": round(md["success"] / md["n"], 3) if md["n"] else 0.0,
                "avg_cost_usd": round(md["cost"] / md["n"], 6) if md["n"] else 0.0,
            }
        result[cls] = {
            "samples": d["n"],
            "success_rate": round(sr, 3),
            "avg_cost_usd": round(avg_cost, 6),
            "avg_latency_ms": round(avg_latency, 1),
            "meets_min_samples": d["n"] >= MIN_SAMPLES,
            "models": models,
        }
    return result


def propose(analysis: dict, schema: dict) -> list[dict]:
    """Propose routing changes based on analysis. Conservative: only propose
    when a non-primary model has clearly better success/cost and >= min samples."""
    proposals = []
    for cls, d in analysis.items():
        if not d["meets_min_samples"]:
            continue
        current_primary = schema["task_classes"][cls]["primary"]
        # Find best model by success rate, then lowest cost.
        candidates = sorted(d["models"].items(), key=lambda kv: (-kv[1]["success_rate"], kv[1]["avg_cost_usd"]))
        if not candidates:
            continue
        best_model, best_stats = candidates[0]
        if best_model == current_primary:
            continue
        # Only propose if best has strictly better success rate OR (equal success + lower cost).
        if current_primary not in d["models"]:
            # No samples for the current primary: cannot compare fairly; skip.
            continue
        cur_stats = d["models"][current_primary]
        if best_stats["success_rate"] > cur_stats["success_rate"] + 0.02:
            proposals.append({
                "class": cls,
                "action": "reroute_primary",
                "from": current_primary,
                "to": best_model,
                "reason": f"success_rate {best_stats['success_rate']} vs {cur_stats['success_rate']} (n={d['samples']})",
            })
        elif (abs(best_stats["success_rate"] - cur_stats["success_rate"]) < 0.02
              and best_stats["avg_cost_usd"] < cur_stats["avg_cost_usd"] * 0.8):
            proposals.append({
                "class": cls,
                "action": "reroute_primary",
                "from": current_primary,
                "to": best_model,
                "reason": f"equal success, lower cost ${best_stats['avg_cost_usd']} vs ${cur_stats['avg_cost_usd']} (n={d['samples']})",
            })
    return proposals


def main():
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    receipts = load_receipts(7)
    analysis = analyze(receipts)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    proposals = propose(analysis, schema)

    report = {
        "generated": datetime.now().isoformat(),
        "window_days": 7,
        "receipt_count": len(receipts),
        "analysis": analysis,
        "proposals": proposals,
        "note": "Proposals require human approval. Apply manually to routing.schema.json + regenerate OmO config. Never auto-commit.",
    }
    out_path = PROPOSALS / f"{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"Receipts: {len(receipts)}  Classes analyzed: {len(analysis)}  Proposals: {len(proposals)}")
    if proposals:
        for p in proposals:
            print(f"  PROPOSE: {p['class']}: {p['from']} -> {p['to']} ({p['reason']})")
    else:
        print("  No proposals (insufficient samples or current routing optimal).")


if __name__ == "__main__":
    main()
