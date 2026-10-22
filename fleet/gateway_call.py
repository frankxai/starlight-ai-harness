#!/usr/bin/env python3
"""Starlight gateway caller with live 403-catch fallback walking.

Makes a real chat-completions call through the Vercel AI Gateway for a task
class, walking the route chain on 403/quota errors and recording a receipt per
attempt (success or fail). Non-gateway models (opencode/*, omniroute/*) are
skipped here - those live behind the OpenCode harness, not the gateway.

Set STARLIGHT_FORCE_FAIL=1 to simulate a failure on every attempt (tests the
walker without spending beyond the first request).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from route_executor import route, record_receipt

SCHEMA = HERE / "routing.schema.json"
OPENCODE_JSON = Path.home() / ".config" / "opencode" / "opencode.json"
GATEWAY = "https://ai-gateway.vercel.sh/v1/chat/completions"
ATTEMPT_COUNTER = [0]


def gateway_key() -> str:
    """Read the current vck_ Token Pass key from opencode.json (no hardcoding)."""
    text = OPENCODE_JSON.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"vck_[A-Za-z0-9]+", text)
    if not m:
        raise RuntimeError("no vck_ gateway key found in opencode.json")
    return m.group(0)


def pricing() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))["token_pass"]["models"]


def estimate_cost(model: str, usage: dict) -> float:
    p = pricing().get(model)
    if not p:
        return 0.0
    u = usage or {}
    pt = float(u.get("prompt_tokens", 0) or 0)
    ct = float(u.get("completion_tokens", 0) or 0)
    cached = float(u.get("prompt_cache_read_tokens", u.get("cache_read_input_tokens", 0)) or 0)
    uncached = max(pt - cached, 0.0)
    return uncached * p["input"] / 1e6 + cached * p["cached"] / 1e6 + ct * p["output"] / 1e6


def attempt(model: str, key: str, prompt: str, timeout: int = 120) -> tuple[bool, dict]:
    """One gateway call. Returns (ok, details). Ok carries content+cost."""
    body = json.dumps({
        "model": model.removeprefix("vercel/"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }).encode("utf-8")
    req = urllib.request.Request(GATEWAY, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    t0 = time.time()
    try:
        # STARLIGHT_FORCE_FAIL_N=k: simulate the first k attempts failing
        # (for testing the walker without burning quota on every hop).
        n = int(os.environ.get("STARLIGHT_FORCE_FAIL_N", "0"))
        attempt_no = ATTEMPT_COUNTER[0]
        ATTEMPT_COUNTER[0] += 1
        if attempt_no < n:
            raise urllib.error.HTTPError(GATEWAY, 403, "forced test failure", None, None)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, {"model": model, "status": e.code, "error": str(e.reason),
                       "latency_ms": int((time.time() - t0) * 1000)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, {"model": model, "status": None, "error": str(e),
                       "latency_ms": int((time.time() - t0) * 1000)}
    latency = int((time.time() - t0) * 1000)
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    cost = estimate_cost(model, data.get("usage", {}))
    gw = (data.get("provider_metadata") or {}).get("gateway", {})
    provider = gw.get("finalProvider") or (gw.get("routing") or {}).get("finalProvider", "")
    return True, {"model": model, "content": content, "cost_usd": cost,
                  "latency_ms": latency, "provider": provider}


def call(task_class: str, prompt: str) -> dict:
    r = route(task_class)
    key = gateway_key()
    chain = [m for m in r["chain"] if m.startswith("vercel/")]
    if not chain:
        raise RuntimeError(
            f"task class '{task_class}' has no gateway-reachable models: {r['chain']}")
    primary = r["primary"]
    failed = []
    for i, model in enumerate(chain):
        ok, out = attempt(model, key, prompt)
        record_receipt(task_class, model, model != primary,
                       out.get("cost_usd", 0.0), out["latency_ms"],
                       ok, out.get("provider", ""))
        if ok:
            out["attempts"] = i + 1
            out["fallback_used"] = model != primary
            return out
        failed.append(out)
    raise RuntimeError(json.dumps({"error": "all gateway models failed",
                                   "task_class": task_class, "failed": failed}))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Gateway call with 403 fallback walking.")
    ap.add_argument("task_class", help="scout/review/bulk/code/hard-reasoning/vision/ultrabrain")
    ap.add_argument("prompt")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        out = call(args.task_class, args.prompt)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"model={out['model']}  attempts={out['attempts']}  "
              f"fallback={out['fallback_used']}  cost=${out['cost_usd']:.6f}  "
              f"latency={out['latency_ms']}ms")
        print(f"reply: {out['content'][:200]}")


if __name__ == "__main__":
    main()
