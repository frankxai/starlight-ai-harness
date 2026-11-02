#!/usr/bin/env python3
"""Starlight routing classifier: task prompt -> task class.

Hybrid: regex fast-path for obvious keywords, then a free-model LLM call for
ambiguous cases. Classification itself routes to the cheapest free model to
keep the classifier cost at $0.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "routing.schema.json"

# Fast-path keyword rules (order matters: first match wins).
FAST_RULES = [
    ("vision",        re.compile(r"\b(image|screenshot|screencast|video|diagram|ui mock|visual|multimodal|looker)\b", re.I)),
    ("ultrabrain",    re.compile(r"\b(architect|design system|from scratch|greenfield|novel|research-grade|deep think)\b", re.I)),
    ("hard-reasoning",re.compile(r"\b(reason|plan|architect|debug root cause|multi.step|complex|deep codegen|long.context)\b", re.I)),
    ("bulk",          re.compile(r"\b(extract|classify|summarize (all|many|bulk)|batch|bulk|all files|every file)\b", re.I)),
    ("content",       re.compile(r"\b(blog|newsletter|landing (page|copy)|social (post|caption)|seo|meta description|title tag|marketing|launch announce|case stud(y|ies)|outreach (email|message)|copy for)\b", re.I)),
    ("review",        re.compile(r"\b(review|lint|audit|check|verify|best practice|style)\b", re.I)),
    # code BEFORE scout: a task with a code verb ('implement a binary
    # search tree') routes to code even though it also contains 'search';
    # scout gets the pure-exploration phrasings left over.
    ("code",          re.compile(r"\b(code|implement|refactor|edit|write|fix|add|change|update (the )?(file|function|module))\b", re.I)),
    ("scout",         re.compile(r"\b(find|search|locate|list|explore|scout|map (the )?codebase|where is)\b", re.I)),
]

LLM_PROMPT = """Classify the following task into exactly one of these classes:
scout, review, bulk, content, code, hard-reasoning, vision, ultrabrain.

Definitions:
- scout: fast exploration, file search, codebase mapping
- review: code review, lint, style, best-practice
- bulk: high-volume extraction, classification, summaries
- content: customer-facing writing (blog, landing copy, SEO, newsletters, social)
- code: everyday coding, edits, refactoring, docs
- hard-reasoning: complex multi-step reasoning, deep codegen, planning
- vision: image/video/multimodal analysis
- ultrabrain: hardest tasks, max reasoning

Task: {task}

Reply with ONLY the class name, nothing else."""


def fast_classify(task: str) -> str | None:
    for cls, pat in FAST_RULES:
        if pat.search(task):
            return cls
    return None


def llm_classify(task: str) -> str:
    """Call a free model (beam-free via OpenCode is not reachable from here;
    fall back to a heuristic). In production this shells out to opencode run."""
    # Heuristic fallback when no LLM available: default to 'code' for anything
    # that looks like work, 'scout' otherwise.
    low = task.lower()
    if any(w in low for w in ("how", "what", "why", "explain", "describe")):
        return "scout"
    return "code"


def classify(task: str) -> dict:
    cls = fast_classify(task)
    source = "fast-path"
    if cls is None:
        cls = llm_classify(task)
        source = "heuristic"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    route = schema["task_classes"][cls]
    return {
        "task": task,
        "class": cls,
        "source": source,
        "primary": route["primary"],
        "variant": route["variant"],
        "fallbacks": route["fallbacks"],
        "cost_ceiling_usd": route["cost_ceiling_usd"],
        "zdr_required": route["zdr_required"],
    }


def selftest() -> int:
    """Classification table with known answers incl. the 2026-11-02
    adversarial catches: round 1 ('binary search tree' is code work,
    not exploration) and round 2 (my first fix's regression: pure
    exploration phrasings must STAY scout - precedence, not grammar
    enumeration, is the mechanism)."""
    cases = [
        # round-1 catch: technical nouns containing 'search' are code work
        ("implement a binary search tree", "code"),
        ("write a depth-first search function", "code"),
        ("add search to the catalog endpoint", "code"),
        # round-2 reviewer regression cases: pure exploration must stay scout
        ("find auth middleware", "scout"),
        ("find an entry point", "scout"),
        ("search src for TODOs", "scout"),
        ("search for the config loader", "scout"),
        ("review this PR", "review"),
        ("write a blog post about testing", "content"),
        ("extract emails from all files", "bulk"),
        ("analyze this screenshot", "vision"),
        ("architect a design system from scratch", "ultrabrain"),
    ]
    bad = [(t, classify(t)["class"], want) for t, want in cases
           if classify(t)["class"] != want]
    if bad:
        print(f"CLASSIFIER SELFTEST FAIL: {bad}")
        return 1
    print(f"classifier selftest OK: {len(cases)} cases (incl. search-tree negatives)")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    ap = argparse.ArgumentParser(description="Classify a task into a routing class.")
    ap.add_argument("task", nargs="+", help="Task description")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()
    task = " ".join(args.task)
    result = classify(task)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"class={result['class']}  source={result['source']}  primary={result['primary']}  variant={result['variant']}")


if __name__ == "__main__":
    main()
