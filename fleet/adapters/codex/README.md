# codex adapter

One file: `hook_receipt.py` (notify hook). Every completed Codex turn
appends one metadata-only receipt row to the shared ledger — same row
shape as the other adapters (source: `codex-notify`), so
`cross_harness.py` counts codex rows with zero changes.

## Wire it (GATE - Frank's button, never auto-edited)

`~/.codex/config.toml`:

```toml
notify = ["<python>", "<repo>/fleet/adapters/codex/hook_receipt.py"]
```

Codex passes the event JSON as one argument; the hook also accepts it on
stdin. Point `STARLIGHT_RECEIPTS_PATH` at the Syncthing-shared receipts
dir so every machine's rows land in one estate ledger.

## Privacy

Whitelisted metadata only: thread/session id, model, cost, duration,
token usage. Never prompt content, never transcript bytes.

## Battery rows

Receipts are the honesty layer; graded battery rows for codex models come
from running the OmS battery (`harness_eval.py`) with codex models via
the gateway rail.