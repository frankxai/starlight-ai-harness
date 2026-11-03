#!/usr/bin/env python3
"""Weekly routing review: parse receipts + token-tracker reports, propose routing
diffs. Writes fleet/routing-proposals/YYYY-MM-DD.json for human approval.
Never auto-commits (money/merges stay on the human's button).

Freshness contract: prep (shared with nightly_eval) fast-forwards to
reviewed main first; a diverged or local-ahead checkout REFUSES (exit 2)
instead of reviewing routing on stale code - the 2026-11-03 finding:
the Sunday 04:00 schtasks run executed the pinned Aug-3 checkout and
measured an empty window without refusing.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import nightly_eval as _ne  # prep: the shared freshness contract

HERE = Path(__file__).resolve().parent
RECEIPTS = HERE / "receipts" / "routing.jsonl"
PROPOSALS = HERE / "routing-proposals"
SCHEMA = HERE / "routing.schema.json"
MEMORY = HERE / "memory" / "routing-memory.json"
MIN_SAMPLES = 20


def load_receipts(days: int = 7) -> tuple[list[dict], int]:
    """Well-formed route receipts from the trailing window, plus the number of
    malformed lines skipped. The ledger is an append log: a truncated or
    hand-edited line is data corruption, not a fatal event - crashing on it
    used to kill the whole Monday 04:00 run (reproduced 2026-11-02)."""
    if not RECEIPTS.exists():
        return [], 0
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out = []
    bad = 0
    for line in RECEIPTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            float(r["cost_usd"])
            int(r["latency_ms"])
            int(r["success"])
            r["task_class"]
            r["used"]
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except (KeyError, ValueError, TypeError):
            bad += 1
            continue
        if ts >= cutoff:
            out.append(r)
    return out, bad


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


def load_memory() -> dict:
    """Memory plane (ladder phase 4): per-model cards from graded battery
    rows, written by memory_bridge.py. Empty when not yet generated."""
    if not MEMORY.exists():
        return {}
    doc = json.loads(MEMORY.read_text(encoding="utf-8"))
    return {m: {"pass_rate": c.get("pass_rate"), "n_rows": c.get("n_rows"),
                "weak_tasks": c.get("weak_tasks")}
            for m, c in doc.get("cards", {}).items()}


def selftest() -> int:
    """Synthetic ledger with one corrupt line and one fieldless line: the
    review must keep going, skip them, and count the skips - never crash
    (the pre-fix behavior killed the whole Monday run)."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="wrr-selftest-"))
    ledger = tmp / "routing.jsonl"
    good = json.dumps({"ts": "2026-11-01T12:00:00+00:00", "task_class": "code",
                       "used": "m1", "fallback_used": False, "cost_usd": 0.01,
                       "latency_ms": 100, "success": True, "provider": "",
                       "machine": ""},
                      )
    bad_json = "{not valid json"
    bad_fields = json.dumps({"ts": "2026-11-01T13:00:00+00:00"})
    ledger.write_text("\n".join([good, bad_json, bad_fields]) + "\n",
                      encoding="utf-8")
    global RECEIPTS
    old = RECEIPTS
    RECEIPTS = ledger
    try:
        rows, skipped = load_receipts(7)
    finally:
        RECEIPTS = old
    if len(rows) != 1 or skipped != 2:
        print(f"weekly selftest FAIL: rows={len(rows)} skipped={skipped} "
              f"(want 1, 2)")
        return 1
    print("weekly selftest OK: corrupt + fieldless ledger lines skipped "
          "and counted, run survives")

    # prep refusal: main must exit 2 BEFORE any file writes (fail-closed
    # freshness - a stale checkout never produces proposals)
    old_prep, old_argv = _ne.prep, sys.argv[:]
    _ne.prep = lambda: 2
    sys.argv = ["weekly_routing_review.py"]
    try:
        try:
            main()
            rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    finally:
        _ne.prep = old_prep
        sys.argv = old_argv
    if rc != 2:
        print(f"weekly selftest FAIL: prep refusal exited {rc} (want 2)")
        return 1
    print("weekly selftest OK: prep refusal exits 2 before any writes")

    # prep advanced the checkout -> the process MUST re-exec itself on
    # the reviewed revision (never keep running the stale loaded code)
    class _Restart(Exception):
        pass

    shas = iter(["old", "new"])
    real_execv, real_sha = os.execv, _head_sha
    os.execv = lambda p, a: (_ for _ in ()).throw(_Restart())
    globals()["_head_sha"] = lambda: next(shas)
    old_prep = _ne.prep
    _ne.prep = lambda: 0
    sys.argv = ["weekly_routing_review.py"]
    try:
        main()
        restarted = False
    except _Restart:
        restarted = True
    finally:
        os.execv = real_execv
        globals()["_head_sha"] = real_sha
        _ne.prep = old_prep
    if not restarted:
        print("weekly selftest FAIL: advancing prep must trigger re-exec")
        return 1
    print("weekly selftest OK: advancing prep triggers re-exec")
    return 0


def _head_sha() -> str:
    import subprocess
    p = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return p.stdout.strip()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    # 0. content freshness: never review routing on stale code. In a
    # diverged or local-ahead checkout this exits 2 before any analysis.
    pre = _head_sha()
    rc = _ne.prep()
    if rc != 0:
        print(f"[weekly] prep rc={rc} - review skipped entirely "
              "(stale content must not produce proposals)")
        raise SystemExit(2)
    # prep may have fast-forwarded THIS file on disk: the process still
    # runs the old load_receipts/analyze/propose it loaded at start
    # (2026-11-03 review finding). Re-exec so the reviewed revision's
    # code is what actually executes; the second pass ff's to a no-op.
    if pre and _head_sha() != pre:
        print("[weekly] prep advanced the checkout - re-exec on the "
              "reviewed revision")
        os.execv(sys.executable, [sys.executable,
                                  str(Path(__file__).resolve())] + sys.argv[1:])
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    receipts, bad = load_receipts(7)
    analysis = analyze(receipts)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    proposals = propose(analysis, schema)
    memory = load_memory()

    # annotate each proposal with both models' battery memory, so the
    # human approval decision cites both evidence planes, not folklore
    for p in proposals:
        p["memory"] = {m: memory.get(m) for m in (p["from"], p["to"])}

    report = {
        "generated": datetime.now().isoformat(),
        "window_days": 7,
        "receipt_count": len(receipts),
        "receipts_skipped_malformed": bad,
        "analysis": analysis,
        "proposals": proposals,
        "memory": memory,
        "note": "Proposals require human approval. Apply manually to routing.schema.json + regenerate OmO config. Never auto-commit.",
    }
    out_path = PROPOSALS / f"{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"Receipts: {len(receipts)} (malformed skipped: {bad})  "
          f"Classes analyzed: {len(analysis)}  Proposals: {len(proposals)}")
    if memory:
        cards = ", ".join(
            "{} {}".format(
                m,
                "{:.1%}".format(c["pass_rate"])
                if c.get("pass_rate") is not None else "n/a")
            for m, c in memory.items())
        print(f"Memory plane: {len(memory)} model cards ({cards})")
    else:
        print("Memory plane: absent (run python fleet/memory_bridge.py)")
    if proposals:
        for p in proposals:
            print(f"  PROPOSE: {p['class']}: {p['from']} -> {p['to']} ({p['reason']})")
    else:
        print("  No proposals (insufficient samples or current routing optimal).")


if __name__ == "__main__":
    main()
