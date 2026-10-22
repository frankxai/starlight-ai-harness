#!/usr/bin/env python3
"""Starlight harness eval: standardized task battery with ground truth.

The measurement layer for the routing brain and every harness config. Each task
has a deterministic checker (no LLM judging), so scores are objective.

Battery v2.1 (12 tasks):
  math            multi-step arithmetic with one true answer
  puzzle          ordering logic with one true answer
  instruction     strict output-format obedience
  code            real Python code, executed against asserts
  extraction      three emails, order + spelling
  json-format     strict JSON object
  date-arithmetic working-day date math
  regex           regex authored + executed against fixed cases
  counter         ceiling division
  unit-chain      units + floor division
  table-filter    filtering with deterministic order

Usage:
  python harness_eval.py run-gateway --model vercel/zai/glm-5.3 [--tasks math,code]
  python harness_eval.py record --model opencode/beam-free --task math --ok 1 [--latency 900]
  python harness_eval.py report
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SCHEMA = HERE / "routing.schema.json"
EVALS = HERE / "evals"
RESULTS = EVALS / "eval_results.jsonl"
OPENCODE_JSON = Path.home() / ".config" / "opencode" / "opencode.json"
GATEWAY = "https://ai-gateway.vercel.sh/v1/chat/completions"

# ---------------- check helpers (all deterministic) ----------------

def _last_number(text: str) -> str:
    nums = re.findall(r"-?\d+", text)
    return nums[-1] if nums else ""


def _last_word(text: str) -> str:
    words = re.findall(r"[A-Za-z]+", text)
    return words[-1].lower() if words else ""


def _last_date(text: str) -> str:
    return (re.findall(r"\d{4}-\d{2}-\d{2}", text) or [""])[-1]


def _extract_emails(text: str) -> str:
    found = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return ",".join(e.rstrip(".").lower() for e in found[:3])


def _last_json_block(text: str):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _last_letter_csv(text: str) -> str:
    hits = re.findall(r"\b[A-Z](?:\s*,\s*[A-Z])*\b", text)
    return hits[-1].replace(" ", "") if hits else ""


_REGEX_CASES = ["Abc-123", "abc-123", "Ab-12", "Abc-1234", "Abc-12d", "ABC-123"]
_REGEX_WANT = [True, False, False, False, False, False]


def _check_regex(text: str) -> bool:
    m = re.search(r'r?["\']([^"\']+)["\']', text)
    if not m:
        return False
    try:
        rx = re.compile(m.group(1))
    except re.error:
        return False
    got = [bool(re.fullmatch(rx, s)) for s in _REGEX_CASES]
    return got == _REGEX_WANT


def _check_code(text: str) -> bool:
    m = re.search(r"def\s+fahrenheit_to_celsius", text)
    if not m:
        return False
    snippet = "def fahrenheit_to_celsius" + text[m.end():]
    env = {}
    try:
        exec(snippet, env)
        f = env.get("fahrenheit_to_celsius")
        if not callable(f):
            return False
        return (abs(f(32) - 0.0) < 1e-6 and abs(f(212) - 100.0) < 1e-6
                and abs(f(98.6) - 37.0) < 1e-3)
    except Exception:
        return False


def _check_html(text: str) -> bool:
    """Front-end discipline probe: doctype + lang + viewport + alt + label-for +
    present interactive element. Deterministic; measures trained slop habits."""
    lower = text.strip().lower()
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.doctype = False
            self.lang = False
            self.viewport = False
            self.img_total = 0
            self.img_bare = 0
            self.label_for = 0
            self.action_tag = 0

        def handle_decl(self, decl):
            if decl.strip().lower().startswith("doctype html"):
                self.doctype = True

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            t = tag.lower()
            if t == "html" and (d.get("lang") or "").strip():
                self.lang = True
            if t == "meta" and d.get("name", "").lower() == "viewport":
                self.viewport = True
            if t == "img":
                self.img_total += 1
                if not (d.get("alt") or "").strip():
                    self.img_bare += 1
            if t == "label" and (d.get("for") or "").strip():
                self.label_for += 1
            if t in ("button", "a", "input", "textarea", "select"):
                self.action_tag += 1

    p = P()
    p.feed(text)
    return (p.doctype and p.lang and p.viewport and p.img_total >= 1
            and p.img_bare == 0 and p.label_for >= 1 and p.action_tag > 0)


# ---------------- battery ----------------

TASKS = {
    "math": {
        "prompt": ("A factory produces 4 widgets per hour for the first 3 hours, "
                   "then 7 widgets per hour for the next 2 hours. A quality check "
                   "rejects 1 in every 10 widgets (rounded down). How many widgets "
                   "pass? Reply with ONLY the number, nothing else."),
        "truth": "24",
        "check": lambda t: _last_number(t) == "24",
    },
    "puzzle": {
        "prompt": ("Anna is taller than Bea. Cass is taller than Anna. Dan is "
                   "shorter than Bea. Who is the third tallest? Reply with one "
                   "name only, nothing else."),
        "truth": "Bea",
        "check": lambda t: _last_word(t) == "bea",
    },
    "instruction": {
        "prompt": "Reply with exactly the word BANANA. Do not add anything else.",
        "truth": "BANANA",
        "check": lambda t: t.strip().strip('"`').upper() == "BANANA",
    },
    "code": {
        "prompt": ("Write a Python function fahrenheit_to_celsius(f) that converts "
                   "Fahrenheit to Celsius and returns a float. Reply with ONLY the "
                   "Python function, no prose, no markdown."),
        "truth": "exec(3 asserts)",
        "check": _check_code,
    },
    "extraction": {
        "prompt": ("From this memo, extract the three email addresses in order of "
                   "appearance: \"Ping Ana at ana.kovac@brightloop.io. Finance wants "
                   "billing@shadowguard.eu before Friday; cc devon.smith@quartz-lab.org.\" "
                   "Reply with only the three addresses, comma-separated, nothing else."),
        "truth": "ana.kovac@brightloop.io,billing@shadowguard.eu,devon.smith@quartz-lab.org",
        "check": lambda t: _extract_emails(t) == TASKS["extraction"]["truth"],
    },
    "json-format": {
        "prompt": ("Reply with only a JSON object: {\"city\": \"Vienna\", \"year\": 2026, "
                   "\"ok\": true} exactly. No markdown, no code fences, nothing else."),
        "truth": '{"city":"Vienna","year":2026,"ok":true}',
        "check": lambda t: _last_json_block(t) == {
            "city": "Vienna", "year": 2026, "ok": True},
    },
    "date-arithmetic": {
        "prompt": ("Today is Wednesday, 21 October 2026. A permit filed today takes "
                   "17 working days (Mon-Fri; no holidays). What calendar date is the "
                   "last day of the 17th working day? Reply with only the date as "
                   "YYYY-MM-DD."),
        "truth": "2026-11-13",
        "check": lambda t: _last_date(t) == "2026-11-13",
    },
    "regex": {
        "prompt": ("Write a Python regex string (as a literal, e.g. r\"...\") that "
                   "matches exactly strings starting with a capital letter followed by "
                   "one or more lowercase letters, then a hyphen, then exactly three "
                   "digits, and nothing else (full string). Reply with only the "
                   "r-string literal."),
        "truth": r"^[A-Z][a-z]+-\d{3}$",
        "check": _check_regex,
    },
    "counter": {
        "prompt": ("A machine advances 7 units per cycle, starting from 0. How many "
                   "full cycles until it has reached or passed 27 units? Reply with "
                   "only the number."),
        "truth": "4",
        "check": lambda t: _last_number(t) == "4",
    },
    "unit-chain": {
        "prompt": ("A cable is 2.5 meters long. You need pieces of exactly 40 "
                   "centimeters each; offcuts are discarded. How many full pieces do "
                   "you get? Reply with only the number."),
        "truth": "6",
        "check": lambda t: _last_number(t) == "6",
    },
    "table-filter": {
        "prompt": ("Rows: [A 12] [B 7] [C 12] [D 3] [E 9] [F 15]. List the row "
                   "letters whose number is at most 9, in alphabetical order, "
                   "comma-separated, uppercase. Reply with only those letters."),
        "truth": "B,D,E",
        "check": lambda t: _last_letter_csv(t) == "B,D,E",
    },
    "html-design": {
        "prompt": ("Write a complete minimal HTML page. Requirements: starts with "
                   "<!doctype html>; the <html> tag has a lang attribute; includes "
                   "a viewport meta tag; contains exactly one <img> with a non-empty "
                   "alt attribute; contains one <input> bound with <label for=...>; "
                   "contains one <button> element. Reply with only the HTML code, "
                   "nothing else."),
        "truth": "doctype+lang+viewport+alt+label-for+button",
        "check": _check_html,
    },
}


# ---------------- gateway call ----------------

def gateway_key() -> str:
    text = OPENCODE_JSON.read_text(encoding="utf-8", errors="replace")
    if not (m := re.search(r"vck_[A-Za-z0-9]+", text)):
        raise RuntimeError("no vck_ gateway key in opencode.json")
    return m.group(0)


def gateway_call(model: str, prompt: str, timeout: int = 120) -> dict:
    key = gateway_key()
    # max_tokens=2000: reasoning-style models may emit long CoT before the final
    # answer; a small cap yields empty content and would measure harness
    # truncation instead of model quality (caught on date-arithmetic/regex).
    body = json.dumps({
        "model": model.removeprefix("vercel/"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
    }).encode("utf-8")
    req = urllib.request.Request(GATEWAY, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
    return {
        "content": (data.get("choices") or [{}])[0].get("message", {}).get("content", ""),
        "latency_ms": int((time.time() - t0) * 1000),
        "usage": data.get("usage", {}),
    }


def _schema_price(model: str) -> dict | None:
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        return schema["token_pass"]["models"].get(model)
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def usage_cost(model: str, usage: dict) -> float | None:
    """Derive $cost from the gateway usage object using schema pricing ($/Mtok)."""
    p = _schema_price(model)
    if not p:
        return None
    t_in = usage.get("prompt_tokens", 0) or 0
    t_cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    t_out = usage.get("completion_tokens", 0) or 0
    return round(
        (t_in - t_cached) / 1e6 * p.get("input", 0.0)
        + t_cached / 1e6 * p.get("cached", 0.0)
        + t_out / 1e6 * p.get("output", 0.0), 6)


def append(result: dict) -> None:
    EVALS.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


def load_results() -> list[dict]:
    if not RESULTS.exists():
        return []
    return [json.loads(line) for line in
            RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]


def report() -> str:
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "ok": 0, "latencies": []})
    by_model = defaultdict(lambda: {"n": 0, "ok": 0})
    for r in load_results():
        a = agg[(r["model"], r["task"])]
        a["n"] += 1
        a["ok"] += int(r["ok"])
        if r.get("latency_ms"):
            a["latencies"].append(r["latency_ms"])
        m = by_model[r["model"]]
        m["n"] += 1
        m["ok"] += int(r["ok"])
    lines = ["| model | task | pass rate | avg latency | n |", "|---|---|---|---|---|"]
    for (model, task), a in sorted(agg.items()):
        lat = sum(a["latencies"]) / len(a["latencies"]) if a["latencies"] else None
        lines.append(f"| {model} | {task} | {a['ok'] / a['n']:.0%} | "
                     f"{f'{lat:,.0f} ms' if lat else 'n/a'} | {a['n']} |")
    lines += ["", "### Overall", "| model | pass rate | n |", "|---|---|---|"]
    for model, m in sorted(by_model.items()):
        lines.append(f"| {model} | {m['ok'] / m['n']:.0%} | {m['n']} |")
    return "\n".join(lines)


def cmd_run_gateway(args):
    ts = datetime.now(timezone.utc).isoformat()
    tasks = args.tasks.split(",") if args.tasks else list(TASKS)
    results = {}
    for task in tasks:
        spec = TASKS[task]
        try:
            r = gateway_call(args.model, spec["prompt"])
        except (RuntimeError, urllib.error.URLError) as e:
            results[task] = {"error": str(e)}
            append({"ts": ts, "model": args.model, "task": task, "ok": False,
                    "error": str(e), "harness": "gateway"})
            continue
        ok = bool(spec["check"](r["content"]))
        u = r.get("usage", {}) or {}
        reasoning_only = bool(u.get("completion_tokens", 0)) and not r["content"].strip()
        rec = {"ts": ts, "model": args.model, "task": task, "ok": ok,
               "latency_ms": r["latency_ms"], "harness": "gateway",
               "reply_sample": r["content"][:300]}
        if reasoning_only:
            rec["reasoning_only"] = True
            ok = False  # an empty answer can never pass, whatever caused it
            rec["ok"] = False
        u = {k: u.get(k) for k in ("prompt_tokens", "completion_tokens")} if u else None
        if u is not None and (u.get("prompt_tokens") or u.get("completion_tokens")):
            rec["usage"] = u
            cost = usage_cost(args.model, u)
            if cost is not None:
                rec["cost_usd"] = cost
        append(rec)
        results[task] = {"ok": ok, "latency_ms": r["latency_ms"]}
    print(json.dumps(results, indent=2))


def cmd_record(args):
    append({"ts": datetime.now(timezone.utc).isoformat(), "model": args.model,
            "task": args.task, "ok": bool(args.ok),
            "latency_ms": args.latency, "harness": args.harness})
    print("recorded")


def cmd_selftest(args):
    """Verify the checkers themselves against ground truth (no model calls)."""
    canned = {
        "math": "24", "puzzle": "Bea", "instruction": "BANANA",
        "extraction": "ana.kovac@brightloop.io,billing@shadowguard.eu,devon.smith@quartz-lab.org",
        "json-format": '{"city": "Vienna", "year": 2026, "ok": true}',
        "date-arithmetic": "2026-11-13", "regex": r'r"^[A-Z][a-z]+-\d{3}$"',
        "counter": "4", "unit-chain": "6", "table-filter": "B,D,E",
        "html-design": ('<!doctype html><html lang="en"><head>'
                        '<meta name="viewport" content="width=device-width, '
                        'initial-scale=1"></head><body>'
                        '<img src="hero.png" alt="Dashboard overview">'
                        '<label for="q">Search</label><input id="q">'
                        '<button>Go</button></body></html>'),
    }
    fails = []
    for task, spec in TASKS.items():
        want = canned.get(task)
        if want is None:  # e.g. code: checked separately below
            continue
        if not bool(spec["check"](want)):
            fails.append(task)

    # negative cases: known-slop inputs must FAIL, not pass silently
    _neg_html = ('<!doctype html><html><head><title>x</title></head>'
                 '<body><img src="hero.png">'
                 '<label>Search</label><button>Go</button></body></html>')
    if _check_html(_neg_html):
        fails.append("html-design/accepts-slop(no-lang-viewport-alt)")

    code_ok = _check_code("def fahrenheit_to_celsius(f):\n    return (f - 32) * 5 / 9")
    if not code_ok:
        fails.append("code")
    if fails:
        print(f"SELFTEST FAIL: {fails}")
        raise SystemExit(1)
    print("selftest OK: all 12 checkers pass ground truth, "
          "html-design rejects slop")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("run-gateway")
    g.add_argument("--model", required=True)
    g.add_argument("--tasks", default="")
    g.set_defaults(func=cmd_run_gateway)
    r = sub.add_parser("record")
    r.add_argument("--model", required=True)
    r.add_argument("--task", required=True)
    r.add_argument("--ok", type=int, required=True)
    r.add_argument("--latency", type=int, default=None)
    r.add_argument("--harness", default="opencode-subagent")
    r.set_defaults(func=cmd_record)
    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)
    p = sub.add_parser("report")
    p.set_defaults(func=lambda a: print(report()))
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
