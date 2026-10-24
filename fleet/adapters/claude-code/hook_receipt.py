#!/usr/bin/env python3
"""Claude Code receipt adapter (Stop hook) - same honesty plane as the
OpenCode plugin.

Reads the Stop-hook JSON payload from stdin and appends ONE receipt row
per session to the shared receipts ledger. Fields are WHITELISTED
metadata only (session id, model, cost, duration, token counts) - never
prompt content or transcript bytes.

Row shape matches the OpenCode plugin's so cross_harness.py picks up
claude-code rows with zero changes:

  {"ts": ..., "source": "claude-code-hook", "sessionID": ...,
   "modelID": ..., "agent": "stop", "finish": "stop",
   "tokens": {...}?, "cost_usd": ..., "latency_ms": ...}

Sink resolution (first wins):
  1. STARLIGHT_RECEIPTS_PATH env var (recommended: the Syncthing-shared
     receipts dir so all machines land in one ledger)
  2. <repo>/fleet/receipts/claude-code.jsonl

Wiring (Claude Code settings.json - GATE: Frank's button, never
auto-edited by agents):

  {
    "hooks": {
      "Stop": [
        {
          "hooks": [
            {
              "type": "command",
              "command": "<python> <repo>/fleet/adapters/claude-code/hook_receipt.py"
            }
          ]
        }
      ]
    }
  }

Exit codes: 0 always (a receipt hook must never block a session).
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_SINK = REPO / "fleet" / "receipts" / "claude-code.jsonl"


def receipt_from_payload(payload: dict) -> dict:
    cost = payload.get("cost") or {}
    tokens = payload.get("tokens") or {}
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "claude-code-hook",
        "sessionID": payload.get("session_id") or payload.get("sessionID") or "-",
        "modelID": payload.get("model") or "claude-code",
        "agent": "stop",
        "finish": "stop",
        "cost_usd": cost.get("total_cost_usd"),
        "latency_ms": cost.get("total_duration_ms"),
    }
    if tokens:
        row["tokens"] = {
            k: tokens.get(k) for k in
            ("input", "output", "reasoning", "cache_read", "cache_write")
            if tokens.get(k) is not None
        }
    return row


def main() -> int:
    sink = None
    try:
        raw = sys.stdin.read() or "{}"
        payload = json.loads(raw)
    except Exception:
        payload = {}
    row = receipt_from_payload(payload)
    sink = os.environ.get("STARLIGHT_RECEIPTS_PATH") or str(DEFAULT_SINK)
    try:
        Path(sink).parent.mkdir(parents=True, exist_ok=True)
        with open(sink, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"hook_receipt: sink write failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())