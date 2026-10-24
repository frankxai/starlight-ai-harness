# claude-code adapter

One file: `hook_receipt.py` (Stop hook). Appends one metadata-only receipt
row per Claude Code session to the shared receipts ledger — the same row
shape as the OpenCode plugin, so `cross_harness.py` counts claude-code rows
with zero changes.

## Wire it (GATE - Frank's button, never auto-edited)

`~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:/Python313/python.exe C:/Users/frank/agentic-ops/fleet/adapters/claude-code/hook_receipt.py"
          }
        ]
      }
    ]
  }
}
```

Point `STARLIGHT_RECEIPTS_PATH` at the Syncthing-shared receipts dir so
every machine's rows land in one estate ledger.

## Privacy

Whitelisted metadata only: session id, model, cost, duration, token counts.
Never prompt content, never transcript bytes.

## Battery rows

Receipts are the honesty layer; graded battery rows for the claude-code
model path come from running the OmS battery (`harness_eval.py`) with
claude-code's models via the gateway rail.