#!/usr/bin/env python3
"""Cross-harness comparison: the same battery across execution rails.

Joins two ledgers with no model calls:
  fleet/evals/eval_results.jsonl  battery rows    -> quality: pass/latency per rail+model
  fleet/receipts/opencode.jsonl   plugin receipts -> overhead: $/msg, latency, context weight

Writes fleet/reports/cross-harness-latest.md and the JSON mirror. Receipts measure
harness overhead, not task quality; the battery table is the comparison, and the
shared-models table aligns models that appear on more than one rail.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness_eval import load_results  # noqa: E402

RECEIPTS_DIR = HERE / "receipts"  # all adapter ledgers count (opencode + adapters)
REPORTS = HERE / "reports"


def _agg_battery(rows: list[dict]) -> list[dict]:
    agg = defaultdict(lambda: {"n": 0, "ok": 0, "latencies": [], "costs": [],
                               "tasks": {}})
    for r in rows:
        key = (r.get("harness", "?"), r["model"])
        a = agg[key]
        a["n"] += 1
        a["ok"] += int(bool(r["ok"]))
        if r.get("latency_ms"):
            a["latencies"].append(r["latency_ms"])
        if r.get("cost_usd") is not None:
            a["costs"].append(r["cost_usd"])
        t = a["tasks"].setdefault(r["task"], {"n": 0, "ok": 0})
        t["n"] += 1
        t["ok"] += int(bool(r["ok"]))
    return [
        {
            "harness": h,
            "model": m,
            "n": a["n"],
            "pass_rate": round(a["ok"] / a["n"], 3),
            "avg_latency_ms": int(sum(a["latencies"]) / len(a["latencies"])) if a["latencies"] else None,
            "total_cost_usd": round(sum(a["costs"]), 6) if a["costs"] else None,
            "cost_per_pass_usd": round(sum(a["costs"]) / a["ok"], 6)
                if a["costs"] and a["ok"] else None,
            # fails-only task matrix: full 11 passes every cell is the headline
            "fails": sorted(f"{task} {t['ok']}/{t['n']}"
                            for task, t in a["tasks"].items() if t["ok"] < t["n"]),
        }
        # harness first so rails read together
        for (h, m), a in sorted(agg.items())
    ]


def _agg_receipts() -> list[dict]:
    # every receipts ledger counts: opencode.jsonl (plugin) plus any
    # adapter ledger (claude-code.jsonl, ...) writing the same row shape.
    # Rows carry source (opencode-plugin / claude-code-hook / ...).
    sources: dict = {}
    for ledger in sorted(RECEIPTS_DIR.glob("*.jsonl")):
        if ledger.name == "opencode_events_raw.jsonl":
            continue
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            src = r.get("source") or "opencode-plugin"
            m = r.get("modelID") or "?"
            models = sources.setdefault(src, {})
            a = models.setdefault(m, {"msgs": 0, "cost": 0.0, "latencies": [],
                                      "in": 0, "cache_read": 0, "reasoning": 0,
                                      "finish": defaultdict(int)})
            a["msgs"] += 1
            a["cost"] += r.get("cost_usd") or 0.0
            a["finish"][r.get("finish", "?")] += 1
            if r.get("latency_ms"):
                a["latencies"].append(r["latency_ms"])
            t = r.get("tokens", {}) or {}
            a["in"] += t.get("input", 0)
            a["cache_read"] += t.get("cache_read", 0)
            a["reasoning"] += t.get("reasoning", 0)
    out = []
    for src in sorted(sources):
        for m, a in sorted(sources[src].items()):
            out.append({
                "source": src,
                "modelID": m,
                "msgs": a["msgs"],
                "total_cost_usd": round(a["cost"], 6),
                "mean_cost_per_msg_usd": round(a["cost"] / a["msgs"], 6),
                "agg_latency_ms": int(sum(a["latencies"])),
                "mean_latency_ms": int(sum(a["latencies"]) / len(a["latencies"])) if a["latencies"] else None,
                "mean_input_tokens": a["in"] // a["msgs"],
                "mean_cache_read_tokens": a["cache_read"] // a["msgs"],
                "mean_reasoning_tokens": a["reasoning"] // a["msgs"],
                "finish": dict(sorted(a["finish"].items())),
            })
    return out


def _canonical(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def _shared(battery: list[dict], receipts: list[dict]) -> list[dict]:
    """Models seen on more than one rail, matched by last path segment."""
    rails: dict[str, set] = defaultdict(set)
    for b in battery:
        rails[_canonical(b["model"])].add(f"battery:{b['harness']}")
    for r in receipts:
        rails[_canonical(r["modelID"])].add(f"receipts:{r.get('source', 'opencode-plugin')}")
    return sorted(
        ({"model": m, "rails": sorted(rs)} for m, rs in rails.items() if len(rs) > 1),
        key=lambda s: s["model"],
    )


def build() -> tuple[str, dict]:
    battery = _agg_battery(load_results())
    receipts = _agg_receipts()
    shared = _shared(battery, receipts)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "battery": battery,
        "plugin_receipts": receipts,
        "shared_models": shared,
    }

    md = ["# Cross-harness comparison",
          "",
          f"generated: {data['generated_at']}",
          "",
          "## Battery rows by rail (fleet/evals/eval_results.jsonl)",
          "",
          "| harness | model | n | pass | avg latency | cost ($) | $/pass | task fails |",
          "|---|---|---|---|---|---|---|---|"]
    for b in battery:
        md.append(
            f"| {b['harness']} | {b['model']} | {b['n']} | {b['pass_rate']:.0%} | "
            f"{str(b['avg_latency_ms']) + ' ms' if b['avg_latency_ms'] else 'n/a'} | "
            f"{b['total_cost_usd'] if b['total_cost_usd'] is not None else 'n/a'} | "
            f"{b['cost_per_pass_usd'] if b['cost_per_pass_usd'] is not None else 'n/a'} | "
            f"{', '.join(b['fails']) if b['fails'] else 'none'} |")
    md += ["",
           "## Plugin receipts (fleet/receipts/opencode.jsonl, harness overhead)",
           "",
           "| modelID | msgs | mean $/msg | total $ | mean latency | in tok | cache-read tok | reasoning tok |",
           "|---|---|---|---|---|---|---|---|"]
    for r in receipts:
        md.append(
            f"| {r['modelID']} | {r['msgs']} | {r['mean_cost_per_msg_usd']:.6f} | "
            f"{r['total_cost_usd']:.6f} | {r['mean_latency_ms'] or 'n/a'} ms | "
            f"{r['mean_input_tokens']} | {r['mean_cache_read_tokens']} | {r['mean_reasoning_tokens']} |")
    md += ["",
           "## Models on multiple rails",
           "",
           "| model | rails |", "|---|---|"]
    for s in shared:
        md.append(f"| {s['model']} | {', '.join(s['rails'])} |")
    if not shared:
        md.append("| (none) | |")
    return "\n".join(md) + "\n", data


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    md, data = build()
    (REPORTS / "cross-harness-latest.md").write_text(md, encoding="utf-8")
    (REPORTS / "cross-harness-latest.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
