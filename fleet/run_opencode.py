#!/usr/bin/env python3
"""Run the battery through the OpenCode rail (`opencode run`).

The reply never passes through an LLM judge: the same deterministic checkers from
harness_eval grade stdout. Rows land in eval_results.jsonl tagged
harness=opencode-plugin; per-message cost/latency are captured by the
starlight receipts plugin into receipts/opencode.jsonl (kill switch
STARLIGHT_RECEIPTS=0 stays off).

Usage:
  python fleet/run_opencode.py --model opencode/glm-5.3-flash-free
  python fleet/run_opencode.py --model <model> --tasks math,code
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness_eval as he  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tasks", default="")
    ap.add_argument("--timeout", type=int, default=300, help="per task, seconds")
    args = ap.parse_args()

    tasks = args.tasks.split(",") if args.tasks else list(he.TASKS)
    for task in tasks:
        spec = he.TASKS[task]
        t0 = time.time()
        try:
            # list args, not a shell string: task prompts contain quotes/braces;
            # .cmd shim, not the bare name (python can't exec the .ps1 npm shim)
            proc = subprocess.run(
                ["opencode.cmd", "run", "-m", args.model, spec["prompt"]],
                capture_output=True, text=True, timeout=args.timeout)
            latency_ms = int((time.time() - t0) * 1000)
            if proc.returncode != 0:
                rec = {"ts": datetime.now(timezone.utc).isoformat(),
                       "model": args.model, "task": task, "ok": False,
                       "latency_ms": latency_ms, "harness": "opencode-plugin",
                       "error": (proc.stderr or "")[:300]}
            else:
                ok = bool(spec["check"](proc.stdout))
                rec = {"ts": datetime.now(timezone.utc).isoformat(),
                       "model": args.model, "task": task, "ok": ok,
                       "latency_ms": latency_ms, "harness": "opencode-plugin",
                       "reply_sample": proc.stdout[:300].strip()}
        except (subprocess.TimeoutExpired, OSError) as e:
            latency_ms = int((time.time() - t0) * 1000)
            rec = {"ts": datetime.now(timezone.utc).isoformat(),
                   "model": args.model, "task": task, "ok": False,
                   "latency_ms": latency_ms, "harness": "opencode-plugin",
                   "error": str(e)[:300]}
        he.append(rec)
        print(f"{task}: {'pass' if rec['ok'] else 'fail'} ({latency_ms} ms)")


if __name__ == "__main__":
    main()
