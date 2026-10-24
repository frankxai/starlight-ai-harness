#!/usr/bin/env python3
"""Codex CLI receipt adapter (notify hook) - same honesty plane as the
OpenCode plugin and the claude-code Stop-hook adapter.

Codex CLI's `notify` config calls a program on turn events. Point it here
and every completed turn appends ONE metadata-only receipt row to the
shared receipts ledger. Fields whitelisted (session/thread ids, model,
cost, duration, token usage) - never prompt content or transcript bytes.

Row shape matches the other adapters (source: codex-notify) so
cross_harness.py counts codex rows with zero changes.

Sink resolution (first wins):
  1. STARLIGHT_RECEIPTS_PATH env var
  2. <repo>/fleet/receipts/codex.jsonl

Wiring (~/.codex/config.toml - GATE: Frank's button, never auto-edited):

  notify = ["<python>", "<repo>/fleet/adapters/codex/hook_receipt.py"]

Codex passes one argument: a JSON object of the event. The hook accepts
it via argv[1] OR stdin (whichever carries JSON).

Exit code 0 always (a receipt hook must never block a session).
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_SINK = REPO / "fleet" / "receipts" / "codex.jsonl"


def receipt_from_payload(payload: dict) -> dict:
    usage = payload.get("usage") or payload.get("tokens") or {}
    cost = payload.get("cost") or {}
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "codex-notify",
        "sessionID": (payload.get("thread_id") or payload.get("session_id")
                      or payload.get("sessionID") or "-"),
        "modelID": payload.get("model") or "codex",
        "agent": payload.get("agent") or "codex",
        "finish": payload.get("type") or "turn-complete",
        "cost_usd": cost.get("total_cost_usd") if isinstance(cost, dict) else None,
        "latency_ms": (cost.get("total_duration_ms")
                       if isinstance(cost, dict) else None),
    }
    if usage:
        row["tokens"] = {
            k: usage.get(k) for k in
            ("input", "output", "reasoning", "cache_read", "cache_write")
            if usage.get(k) is not None
        }
    return row


def read_payload() -> dict:
    # argv first (codex notify style), then stdin
    for arg in sys.argv[1:]:
        try:
            return json.loads(arg)
        except Exception:
            continue
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def main() -> int:
    row = receipt_from_payload(read_payload())
    sink = os.environ.get("STARLIGHT_RECEIPTS_PATH") or str(DEFAULT_SINK)
    try:
        Path(sink).parent.mkdir(parents=True, exist_ok=True)
        with open(sink, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"codex hook_receipt: sink write failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())