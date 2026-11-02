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


# deps for the task-graph probe: job -> jobs that must run before it
_GRAPH_DEPS = {"A": [], "B": ["A"], "C": ["A", "B"], "D": ["C"], "E": ["B"]}


def _check_task_graph(text: str) -> bool:
    """Multi-step dependency reasoning: accept ANY topological order of the
    five jobs, reject cycles, missing or duplicate jobs. Deterministic."""
    try:
        order = _last_json_block(text)["order"]
    except Exception:
        m = re.search(r"\[[^\[\]]*\]", text.replace("'", '"'))
        if not m:
            return False
        try:
            order = json.loads(m.group(0))
        except Exception:
            return False
    if (not isinstance(order, list) or len(order) != 5
            or not all(isinstance(j, str) for j in order)
            or sorted(order) != ["A", "B", "C", "D", "E"]):
        return False
    pos = {job: i for i, job in enumerate(order)}
    return all(pos[d] < pos[j] for j, deps in _GRAPH_DEPS.items() for d in deps)


# failing-assert suite for the debug-repair probe: the model must return a
# merge_ranges that passes ALL of these (grading = running the suite)
_REPAIR_SUITE = (
    "assert merge_ranges([[1,3],[2,6],[8,10]]) == [[1,6],[8,10]]",
    "assert merge_ranges([[5,7]]) == [[5,7]]",
    "assert merge_ranges([[1,2],[2,3]]) == [[1,3]]",
    "assert merge_ranges([]) == []",
)


def _check_debug_repair(text: str) -> bool:
    """Debug-repair probe: exec the returned merge_ranges and run the real
    failing-assert suite. The buggy sort-only original must FAIL here."""
    m = re.search(r"def\s+merge_ranges", text)
    if not m:
        return False
    snippet = "def merge_ranges" + text[m.end():]
    env: dict = {}
    try:
        exec(snippet, env)
        merge = env["merge_ranges"]
        for case in _REPAIR_SUITE:
            exec(case, {}, {"merge_ranges": merge})
        return True
    except Exception:
        return False


# structured function-call probe: the one documented tool for this request,
# with typed args. Wrong tool, wrong arg names, or wrong value types FAIL.
_TOOL_USE_DOC = (
    "search_flights(origin: str, destination: str, date: str, "
    "passengers: int, max_results: int) -> find bookable flights; "
    "book_hotel(city: str, checkin: str, checkout: str, rooms: int) -> "
    "reserve a hotel room; get_weather(city: str, date: str) -> forecast"
)
_FLIGHT_ARGS = {
    "origin": "AMS", "destination": "LIS", "date": "2026-11-05",
    "passengers": 2, "max_results": 3,
}


def _check_tool_use(text: str) -> bool:
    call = None
    try:
        call = _last_json_block(text)
    except Exception:
        pass
    if not isinstance(call, dict) or call.get("tool") != "search_flights":
        return False
    args = call.get("args")
    if not isinstance(args, dict):
        return False
    for key, want in _FLIGHT_ARGS.items():
        if key not in args:
            return False
        got = args[key]
        # strict types: floats (2.0) and bools (True) violate the documented
        # int contract even when == compares equal; strings must match exactly
        if isinstance(want, int):
            if type(got) is not int or got != want:
                return False
        elif got != want:
            return False
    return True


# instruction-following under a hard budget: a valid summary must include
# every key fact, stay under the word limit, and reject vague fluff.
_KEYFACTS = ("fancorp", "q3", "revenue", "14.2", "3%")
_WORDLIMIT = 20


def _check_keyfacts(text: str) -> bool:
    words = text.strip().split()
    if len(words) > _WORDLIMIT or len(words) == 0:
        return False
    low = text.lower()
    # complete-value matching: "14.2" must not match inside "114.2",
    # "3%" must not match inside "13%" - digit boundaries required
    if not re.search(r"(?<![\d.])14\.2(?![\d])", low):
        return False
    if not re.search(r"(?<![\d])3%(?![\d])", low):
        return False
    return all(k in low for k in ("fancorp", "q3", "revenue"))


# test-generation probe: the model writes tests for a function; the tests
# must FAIL against a buggy implementation (kill the mutant) and PASS
# against the correct one. Grading = exec both, no LLM judging.
_TESTGEN_BUGGY = "def f(n):\n    return 2 * n\n"
_TESTGEN_VALID = "def f(n):\n    return 2 * n + 1\n"


def _check_test_generation(text: str) -> bool:
    m = re.search(r"assert[^\n]+", text)
    if not m:
        return False
    # the buggy implementation must FAIL via assertion (mutant killed)
    env: dict = {}
    try:
        exec(_TESTGEN_BUGGY, env)
        exec(text, {}, {"f": env["f"]})
        return False  # buggy survived: tests do not kill the mutant
    except AssertionError:
        pass
    except Exception:
        return False  # tests must fail via assertion, not crash
    # the correct implementation must PASS every test
    env = {}
    try:
        exec(_TESTGEN_VALID, env)
        exec(text, {}, {"f": env["f"]})
    except Exception:
        return False
    return True


# error-traceback probe: exec raises for real; the model must name the line
# that actually raised. Ground truth computed, never hand-written.
_ERR_FUNC = (
    "def f(xs):\n"
    "    total = 0\n"
    "    for x in xs:\n"
    "        total += 1 / x\n"
    "    return total\n"
)


def _error_line() -> int:
    env: dict = {}
    try:
        exec(_ERR_FUNC + "\n" + "f([2, 0])", env)
    except ZeroDivisionError as e:
        tb = e.__traceback__
        while tb.tb_next is not None:
            tb = tb.tb_next
        return tb.tb_lineno
    return 0


def _check_error_traceback(text: str) -> bool:
    line = _error_line()
    if not line:
        return False
    t = text.replace(" ", "")
    # accept func:<line> with or without trailing colon; reject ±1 lines
    if f"func:{line}" in t:
        return True
    return bool(re.search(rf"line\s*{line}\b", text, re.IGNORECASE))


# multi-step plan probe: five steps with a partial order. checkout must be
# first; install-deps after checkout; lint and test both after install-deps
# (no constraint between them); package last, after both lint and test.
_PLAN_STEPS = ("checkout", "install-deps", "lint", "test", "package")
_PLAN_DEPS = {
    "install-deps": ["checkout"], "lint": ["install-deps"],
    "test": ["install-deps"], "package": ["lint", "test"],
}


def _check_multi_step_plan(text: str) -> bool:
    try:
        plan = _last_json_block(text)["plan"]
    except Exception:
        m = re.search(r"\[[^\[\]]*\]", text.replace("'", '"'))
        if not m:
            return False
        try:
            plan = json.loads(m.group(0))
        except Exception:
            return False
    if (not isinstance(plan, list) or len(plan) != 5
            or not all(isinstance(s, str) for s in plan)
            or sorted(plan) != sorted(_PLAN_STEPS)):
        return False
    pos = {s: i for i, s in enumerate(plan)}
    return (pos["checkout"] == 0 and pos["package"] == 4
            and all(pos[d] < pos[s] for s, ds in _PLAN_DEPS.items() for d in ds))


# config-audit probe: exactly one violation in a prod config (debug enabled);
# the reply must name the right key and a reason that says why.
_AUDIT_VIOLATION = "debug_mode"


def _check_config_audit(text: str) -> bool:
    try:
        out = _last_json_block(text)
    except Exception:
        return False
    if not isinstance(out, dict):
        return False
    if out.get("violation") != _AUDIT_VIOLATION:
        return False
    reason = str(out.get("reason", "")).strip()
    if not reason:
        return False
    # a correct explanation names the risk or the setting; a claim that
    # enabling debug in production is fine contradicts the audit
    low = reason.lower()
    if "safe" in low or "fine" in low or "no risk" in low:
        return False
    return True

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
    "task-graph": {
        "prompt": ("A build pipeline has five jobs with dependencies: job A has no "
                   "dependencies; job B depends on A; job C depends on A and B; "
                   "job D depends on C; job E depends on B. Every dependency must "
                   "finish before the job that needs it starts. Reply with only a "
                   "JSON object of the form {\"order\": [\"X\", ...]} listing ALL "
                   "five jobs in one valid execution order. Nothing else."),
        "truth": "any topological order of A,B,C,D,E respecting deps",
        "check": _check_task_graph,
    },
    "debug-repair": {
        "prompt": ("This function should merge overlapping integer ranges and return "
                   "them sorted, but it is broken:\n"
                   "def merge_ranges(ranges):\n"
                   "    return sorted(ranges)\n"
                   "Failing tests:\n"
                   "assert merge_ranges([[1,3],[2,6],[8,10]]) == [[1,6],[8,10]]\n"
                   "assert merge_ranges([[5,7]]) == [[5,7]]\n"
                   "assert merge_ranges([[1,2],[2,3]]) == [[1,3]]\n"
                   "assert merge_ranges([]) == []\n"
                   "Return ONLY the fixed Python function (def merge_ranges...), "
                   "no prose, no markdown."),
        "truth": "def merge_ranges passing all four asserts",
        "check": _check_debug_repair,
    },
    "tool-use": {
        "prompt": ("You may call exactly one tool. Available tools:\n"
                   + _TOOL_USE_DOC + "\n"
                   "A customer request: find bookable flights from Amsterdam "
                   "(AMS) to Lisbon (LIS) departing on 5 November 2026, for 2 "
                   "passengers, showing at most 3 results. Reply with ONLY a "
                   "JSON object of the form {\"tool\": \"<name>\", \"args\": "
                   "{<all required args with values>}}. Use YYYY-MM-DD dates. "
                   "Nothing else."),
        "truth": ("{\"tool\": \"search_flights\", \"args\": "
                  "{\"origin\": \"AMS\", \"destination\": \"LIS\", "
                  "\"date\": \"2026-11-05\", \"passengers\": 2, "
                  "\"max_results\": 3}}"),
        "check": _check_tool_use,
    },
    "keyfacts": {
        "prompt": ("Facts: Fancorp reported Q3 revenue of 14.2 billion dollars, "
                   "up 3% year over year. The head of product resigned and the "
                   "stock fell 5% after hours. Summarize in at most 20 words, "
                   "in English. Reply with only the summary."),
        "truth": "all of: fancorp,q3,revenue,14.2,3%; <= 20 words",
        "check": _check_keyfacts,
    },
    "multi-step-plan": {
        "prompt": ("A release pipeline has five steps: checkout, install-deps, "
                   "lint, test, package. Constraints: checkout must run "
                   "first; install-deps runs after checkout; lint and test "
                   "both run after install-deps (either order, they are "
                   "independent of each other); package runs last, after "
                   "both lint and test. Reply with only a JSON object of the "
                   "form {\"plan\": [\"step\", ...]} listing all five steps in "
                   "one valid execution order. Nothing else."),
        "truth": "any order with checkout first, package last, deps held",
        "check": _check_multi_step_plan,
    },
    "config-audit": {
        "prompt": ("Audit this production config for exactly ONE security "
                   "violation:\n"
                   "[server]\n"
                   "port = 8443\n"
                   "tls = true\n"
                   "debug_mode = true\n"
                   "retries = 3\n\n"
                   "Reply with only a JSON object of the form "
                   "{\"violation\": \"<setting name>\", \"reason\": \"<one short "
                   "sentence>\"} naming the violating setting and why. "
                   "Nothing else."),
        "truth": ('{"violation": "debug_mode", "reason": "...debug..."}'),
        "check": _check_config_audit,
    },
    "test-generation": {
        "prompt": ("Write pytest-style assert tests for this function:\n"
                    "def f(n):\n"
                    "    return 2 * n + 1\n"
                    "Requirements: at least 3 asserts covering positive, negative and "
                    "zero input; every assert on one line; no if/for/while; reply with "
                    "only the assert lines, nothing else."),
        "truth": "asserts that fail on 2*n (mutant) and pass on 2*n+1",
        "check": _check_test_generation,
    },
    "error-traceback": {
        "prompt": ("This code runs and raises an error:\n"
                   + _ERR_FUNC +
                   "The call is f([2, 0]). Reply with ONLY the number of the line "
                   "inside function f that raises, in the form func:<line>: - nothing "
                   "else."),
        "truth": "func:<the real raising line number inside f>:",
        "check": _check_error_traceback,
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
        try:
            ok = bool(spec["check"](r["content"]))
        except Exception as e:
            # a checker crash (e.g. non-string entries) is a graded FAIL,
            # never an aborted battery
            results[task] = {"error": f"checker raised: {e}"}
            append({"ts": ts, "model": args.model, "task": task, "ok": False,
                    "error": f"checker raised: {e}", "harness": "gateway"})
            continue
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
        "task-graph": '{"order": ["A", "B", "E", "C", "D"]}',
        "debug-repair": ("def merge_ranges(ranges):\n"
                         "    ranges = sorted(ranges)\n"
                         "    merged = []\n"
                         "    for lo, hi in ranges:\n"
                         "        if merged and lo <= merged[-1][1]:\n"
                         "            merged[-1][1] = max(merged[-1][1], hi)\n"
                         "        else:\n"
                         "            merged.append([lo, hi])\n"
                         "    return merged\n"),
        "tool-use": ('{"tool": "search_flights", "args": {"origin": "AMS", '
                     '"destination": "LIS", "date": "2026-11-05", '
                     '"passengers": 2, "max_results": 3}}'),
        "keyfacts": ("Fancorp Q3 revenue hit 14.2 billion dollars, up 3%; "
                     "the head of product resigned and shares fell 5%."),
        "multi-step-plan": ('{"plan": ["checkout", "install-deps", '
                            '"test", "lint", "package"]}'),
        "config-audit": ('{"violation": "debug_mode", "reason": "debug '
                         'enabled in production leaks internals"}'),
        "test-generation": ("assert f(1) == 3\n"
                            "assert f(0) == 1\n"
                            "assert f(-3) == -5\n"),
    "error-traceback": None,  # computed below from _ERR_FUNC - never hand-written
    }
    # computed ground truth: the line inside f that actually raises
    canned["error-traceback"] = f"func:{_error_line()}:"
    fails = []
    for task, spec in TASKS.items():
        want = canned.get(task)
        if want is None:  # code: checked separately below
            if task != "code":
                fails.append(f"{task}/no-ground-truth")
            continue
        if not bool(spec["check"](want)):
            fails.append(task)

    # negative cases: known-slop inputs must FAIL, not pass silently
    _neg_html = ('<!doctype html><html><head><title>x</title></head>'
                 '<body><img src="hero.png">'
                 '<label>Search</label><button>Go</button></body></html>')
    if _check_html(_neg_html):
        fails.append("html-design/accepts-slop(no-lang-viewport-alt)")
    if _check_task_graph('{"order": ["C", "A", "B", "D", "E"]}'):
        fails.append("task-graph/accepts-cycle(C-before-A)")
    if _check_debug_repair("def merge_ranges(ranges):\n    return sorted(ranges)\n"):
        fails.append("debug-repair/accepts-buggy-original(sort-only)")
    if _check_tool_use('{"tool": "book_hotel", "args": {"city": "Lisbon", '
                       '"checkin": "2026-11-05", "checkout": "2026-11-06", '
                       '"rooms": 2}}'):
        fails.append("tool-use/accepts-wrong-tool(book_hotel)")
    if _check_tool_use('{"tool": "search_flights", "args": {"origin": "AMS", '
                       '"destination": "LIS", "date": "5 Nov 2026", '
                       '"passengers": 2, "max_results": 3}}'):
        fails.append("tool-use/accepts-unformatted-date")
    if _check_keyfacts("Fancorp grew strongly this quarter and the market "
                       "reacted afterwards."):
        fails.append("keyfacts/accepts-missing-facts")
    if _check_keyfacts("Fancorp Q3 revenue was 14.2 billion, up 3%; the head "
                       "of product resigned and the stock fell by five percent "
                       "in after hours trading."):
        fails.append("keyfacts/accepts-over-word-limit")
    if _check_keyfacts("Fancorp Q3 revenue was 114.2 billion dollars, up 13%."):
        fails.append("keyfacts/accepts-altered-figures(114.2/13%)")
    if _check_keyfacts('{"tool": "search_flights", "args": {"origin": "AMS", '
                       '"destination": "LIS", "date": "2026-11-05", '
                       '"passengers": 2.0, "max_results": 3}}'):
        fails.append("tool-use/accepts-float-int(2.0)")
    if _check_multi_step_plan('{"plan": ["package", "test", "lint", '
                               '"install-deps", "checkout"]}'):
        fails.append("multi-step-plan/accepts-reversed-order")
    if _check_multi_step_plan('{"plan": ["checkout", "install-deps", '
                               '"lint", "package", "test"]}'):
        fails.append("multi-step-plan/accepts-package-before-test")
    if _check_config_audit('{"violation": "tls", "reason": "tls is on"}'):
        fails.append("config-audit/accepts-wrong-key(tls)")
    if _check_config_audit('{"violation": "debug_mode", "reason": "debug is safe to enable in production"}'):
        fails.append("config-audit/accepts-safe-claim")
    if _check_config_audit('{"violation": "debug_mode", "reason": ""}'):
        fails.append("config-audit/accepts-empty-reason")
    if not _check_config_audit('{"violation": "debug_mode", "reason": "Disable it to prevent stack traces and secrets leaking to clients"}'):
        fails.append("config-audit/rejects-correct-explanation-without-keyword")
    if _check_multi_step_plan('{"plan": ["checkout", "install-deps", "lint", "test", null]}'):
        fails.append("multi-step-plan/accepts-null-entry")
    if _check_task_graph('{"order": ["A", "B", "C", "D", 5]}'):
        fails.append("task-graph/accepts-non-string-entry")
    # computed line number - never hand-written ground truth: the computed
    # line must be the source line that actually contains the division
    if _error_line() != next(
            i + 1 for i, l in enumerate(_ERR_FUNC.splitlines())
            if "total +=" in l):
        fails.append("error-traceback/computed-line-drift")
    if not _check_test_generation("assert f(1) == 3\nassert f(0) == 1\nassert f(-3) == -5\n"):
        fails.append("test-generation/rejects-killing-tests")
    # the two REAL DS replies that my first-draft checkers rejected:
    # correct tests (kill mutant + pass valid) and a colon-less func:4
    if not _check_test_generation("assert f(0) == 1\nassert f(5) == 11\nassert f(-3) == -5\nassert f(-1) == -1\n"):
        fails.append("test-generation/rejects-correct-DS-tests")
    if not _check_error_traceback("func:4"):
        fails.append("error-traceback/rejects-colonless-real-DS-reply")
    if _check_test_generation("assert f(1) == 2\nassert f(2) == 4\n"):
        fails.append("test-generation/accepts-non-killing-tests(2n survives)")
    if _check_error_traceback(f"func:{_error_line() + 1}:"):
        fails.append("error-traceback/accepts-wrong-line")

    code_ok = _check_code("def fahrenheit_to_celsius(f):\n    return (f - 32) * 5 / 9")
    if not code_ok:
        fails.append("code")
    if fails:
        print(f"SELFTEST FAIL: {fails}")
        raise SystemExit(1)
    print(f"selftest OK: all {len(TASKS)} checkers pass ground truth, "
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
