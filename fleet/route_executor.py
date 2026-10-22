#!/usr/bin/env python3
"""Starlight route executor: given a task class, return the model to use and
walk the fallback chain. Emits a routing receipt (jsonl) per execution.

This module is the *decision* layer. Actual LLM calls happen elsewhere (OpenCode
subagent tool / OmO). The executor returns the chosen model + fallbacks so the
caller can invoke and report back success/failure via record_receipt().
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "routing.schema.json"
RECEIPTS_DIR = Path(__file__).resolve().parent / "receipts"


def load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"routing schema not found: {SCHEMA_PATH}")
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed routing schema: {e}") from e


def route(task_class: str) -> dict:
    schema = load_schema()
    if task_class not in schema.get("task_classes", {}):
        raise ValueError(f"Unknown task class: {task_class}")
    route_def = schema["task_classes"][task_class]
    required = ("primary", "variant", "fallbacks", "cost_ceiling_usd", "zdr_required")
    for k in required:
        if k not in route_def:
            raise ValueError(f"task class '{task_class}' missing field: {k}")
    return {
        "task_class": task_class,
        "primary": route_def["primary"],
        "variant": route_def["variant"],
        "fallbacks": list(route_def["fallbacks"]),
        "cost_ceiling_usd": route_def["cost_ceiling_usd"],
        "zdr_required": route_def["zdr_required"],
        "chain": [route_def["primary"]] + list(route_def["fallbacks"]),
    }


def pick_next(failed: list[str], task_class: str) -> str | None:
    """Walk the fallback chain, skipping already-failed models.

    Returns None when the entire chain is exhausted; callers MUST handle None
    (do not dereference the result without a guard).
    """
    r = route(task_class)
    for m in r["chain"]:
        if m not in failed:
            return m
    return None


def record_receipt(task_class: str, used: str, fallback_used: bool,
                   cost_usd: float, latency_ms: int, success: bool,
                   provider: str = "", machine: str = "") -> Path:
    """Append a routing receipt (jsonl). Atomic + locked to avoid interleaving.

    Validates task_class up front and reuses the resolved primary so a schema
    drift between route() and record_receipt() cannot record a different primary.
    """
    r = route(task_class)  # validates up front
    if used not in r["chain"]:
        raise ValueError(f"used model '{used}' is not in the chain for '{task_class}': {r['chain']}")
    if cost_usd < 0 or latency_ms < 0:
        raise ValueError("cost_usd and latency_ms must be non-negative")
    ceiling = r["cost_ceiling_usd"]
    if cost_usd > ceiling:
        raise ValueError(
            f"cost_usd {cost_usd} exceeds cost ceiling {ceiling} for '{task_class}'"
        )
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    machine = machine or os.environ.get("STARLIGHT_MACHINE", "unknown")
    receipt = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "id": uuid.uuid4().hex[:12],
        "task_class": task_class,
        "primary": r["primary"],
        "used": used,
        "fallback_used": fallback_used,
        "cost_usd": round(cost_usd, 6),
        "latency_ms": latency_ms,
        "success": success,
        "provider": provider,
        "machine": machine,
    }
    path = RECEIPTS_DIR / "routing.jsonl"
    # Atomic append under an exclusive lock (POSIX; on Windows fcntl may be
    # unavailable — fall back to a plain append in that case).
    try:
        import fcntl
        with path.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(receipt) + "\n")
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (ImportError, AttributeError, OSError):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(receipt) + "\n")
    return path


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Route a task class to a model chain.")
    ap.add_argument("task_class", help="Task class (scout/review/bulk/code/hard-reasoning/vision/ultrabrain)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        r = route(args.task_class)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=__import__("sys").stderr)
        raise SystemExit(2)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"class={args.task_class}  primary={r['primary']}  variant={r['variant']}  fallbacks={r['fallbacks']}")


if __name__ == "__main__":
    main()
