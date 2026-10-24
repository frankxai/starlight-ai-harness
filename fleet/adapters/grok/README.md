# grok adapter

One file: `hook_receipt.py` (session-stop rule hook). Every completed Grok
session appends one metadata-only receipt row to the shared ledger — same
row shape as the other adapters (source: `grok-hook`), so
`cross_harness.py` counts grok rows with zero changes.

## Wire it (GATE - Frank's button, never auto-edited)

Grok rule hooks (`~/.grok/settings/hooks.json`):

```json
{
  "session-stop": [
    {
      "type": "command",
      "command": "<python> <repo>/fleet/adapters/grok/hook_receipt.py"
    }
  ]
}
```

The hook accepts the payload via stdin or `argv[1]`. Point
`STARLIGHT_RECEIPTS_PATH` at the Syncthing-shared receipts dir so every
machine's rows land in one estate ledger.

## Privacy

Whitelisted metadata only: session id, model, cost, duration, token usage.
Never prompt content, never transcript bytes.

## Battery rows

Receipts are the honesty layer; graded battery rows for grok models come
from running the OmS battery (`harness_eval.py`) with grok models via the
gateway rail.