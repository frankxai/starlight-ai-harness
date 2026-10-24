#!/usr/bin/env python3
"""Grok CLI receipt adapter (session-stop rule hook) - same honesty plane
as the opencode plugin and the claude-code/codex adapters.

Grok runs rule hooks with a JSON payload. Point the session-stop rule here
and every completed session appends ONE metadata-only receipt row to the
shared ledger. Fields whitelisted (session id, model, cost, duration,
token usage) - never prompt content or transcript bytes.

Row shape matches the other adapters (source: grok-hook) so
cross_harness.py counts grok rows with zero changes.

Sink resolution (first wins):
  1. STARLIGHT_RECEIPTS_PATH env var
  2. <repo>/fleet/receipts/grok.jsonl

Wiring (~/.grok/settings/hooks.json - GATE: Frank's button, never
auto-edited by agents):

  {
    "session-stop": [
      {"type": "command",
       "command": "<python> <repo>/fleet/adapters/grok/hook_receipt.py"}
    ]
  }

The hook accepts the payload via stdin or argv[1]. Exit code 0 always.
"""
from __future__ import annotations

# same contract as the codex adapter; import its payload/sink logic and
# only override the identity fields.
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_SINK = REPO / "fleet" / "receipts" / "grok.jsonl"


def receipt_from_payload(payload: dict) -> dict:
    usage = payload.get("usage") or payload.get("tokens") or {}
    cost = payload.get("cost") or {}
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "grok-hook",
        "sessionID": (payload.get("session_id") or payload.get("sessionID")
                      or "-"),
        "modelID": payload.get("model") or "grok",
        "agent": payload.get("agent") or "grok",
        "finish": payload.get("type") or "session-stop",
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
        print(f"grok hook_receipt: sink write failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())