#!/usr/bin/env python3
"""Starlight credit watchdog: monitor Token Pass spend per key.

Queries `vercel ai-gateway api-keys ls` for every key with a tokenPass flag,
compares spend against routing.schema.json thresholds:
  - soft alert at soft_alert_usd_per_key (default $60)
  - hard stop warning at hard_cap_usd_per_key - $2 ($78; 403 pre-emption)

Alerts append to fleet/receipts/alerts.jsonl (local telemetry). Exit codes:
  0 = all clear, 1 = soft alert, 2 = hard/unknown failure.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "routing.schema.json"
ALERTS = HERE / "receipts" / "alerts.jsonl"


def load_schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def list_token_pass_keys() -> list[dict]:
    """Shell out to vercel CLI; returns [{name, spend, limit}] for Token Pass keys."""
    import shutil
    npx = (shutil.which("npx.cmd") or shutil.which("npx")
           or r"C:\Program Files\nodejs\npx.cmd")
    cmd = [npx, "vercel", "ai-gateway", "api-keys", "ls", "--format", "json",
           "--scope", schema_token_pass()["team"]]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    text = out.stdout + out.stderr
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"no JSON in vercel CLI output: {text[:200]}")
    # The CLI may print banners/trailers around the JSON payload; raw_decode
    # parses exactly the first JSON value.
    data, _end = json.JSONDecoder().raw_decode(text[start:])
    keys = []
    for k in data.get("apiKeys", []):
        if not k.get("metadata", {}).get("tokenPass"):
            continue
        q = k.get("quota", {})
        keys.append({
            "name": k.get("name", "?"),
            "partial": k.get("partialKey", ""),
            "spend": float(q.get("currentSpend", 0)),
            "limit": float(q.get("limitAmount", 0)),
        })
    return keys


def schema_token_pass() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))["token_pass"]


def check() -> dict:
    tp = schema_token_pass()
    soft = tp["soft_alert_usd_per_key"]
    hard_margin = 2.0
    hard = tp["hard_cap_usd_per_key"] - hard_margin
    try:
        keys = list_token_pass_keys()
    except Exception as e:
        return {"status": "error", "detail": str(e),
                "ts": datetime.now(timezone.utc).isoformat()}

    alerts, max_level = [], 0
    for k in keys:
        pct = (k["spend"] / k["limit"] * 100) if k["limit"] else 0.0
        level = 0
        if k["spend"] >= hard:
            level = 2
        elif k["spend"] >= soft:
            level = 1
        max_level = max(max_level, level)
        if level:
            kind = "HARD" if level == 2 else "SOFT"
            alerts.append(
                f"{kind}: {k['name']} at ${k['spend']:.2f}/${k['limit']:.0f} "
                f"({pct:.0f}%)-{' reroute away now' if kind=='HARD' else ' plan reroute'}")
    result = {
        "status": ["ok", "soft", "hard"][max_level],
        "keys": keys,
        "alerts": alerts,
        "total_spend": round(sum(k["spend"] for k in keys), 4),
        "total_limit": round(sum(k["limit"] for k in keys), 2),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if alerts or result["total_spend"] > 0:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with ALERTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
    return result


def main():
    r = check()
    print(json.dumps(r, indent=2))
    code = {"ok": 0, "soft": 1, "hard": 2}.get(r["status"], 2)
    sys.exit(code)


if __name__ == "__main__":
    main()
