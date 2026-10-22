#!/usr/bin/env python3
"""Generate ~/.omo/omo.jsonc from routing.schema.json (single source of truth).

Run after editing routing.schema.json to keep OmO config in sync.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "routing.schema.json"
OMO = Path.home() / ".omo" / "omo.jsonc"

# Map task classes -> OmO agent roles.
AGENT_MAP = {
    "sisyphus": "code",
    "hephaestus": "code",
    "oracle": "hard-reasoning",
    "prometheus": "hard-reasoning",
    "metis": "ultrabrain",
    "atlas": "code",
    "librarian": None,   # free
    "explore": None,     # free
    "multimodal-looker": None,  # free (vision)
    "momus": None,       # free (review)
}

FREE_DEFAULTS = {
    "librarian": "opencode/beam-free",
    "explore": "opencode/mimo-v2.6-flash-free",
    "multimodal-looker": "opencode/nemotron-3.5-super-vl-free",
    "momus": "opencode/deepseek-v4.1-flash-free",
}

CATEGORY_MAP = {
    "visual-engineering": "vision",
    "ultrabrain": "ultrabrain",
    "deep": "hard-reasoning",
    "quick": None,
    "unspecified-low": None,
    "unspecified-high": "code",
    "writing": "code",
    "research": "hard-reasoning",
    "bulk-extraction": "bulk",
    "code-review": None,
}

FREE_CAT = {
    "visual-engineering": "opencode/nemotron-3.5-super-vl-free",
    "quick": "opencode/mimo-v2.6-flash-free",
    "unspecified-low": "opencode/beam-free",
    "code-review": "opencode/deepseek-v4.1-flash-free",
}

REASONING = {
    "sisyphus": "high", "hephaestus": "medium", "oracle": "high",
    "prometheus": "high", "metis": "max", "atlas": "high",
}


def build(schema: dict) -> dict:
    classes = schema["task_classes"]
    agents = {}
    for agent, cls in AGENT_MAP.items():
        if cls is None:
            primary = FREE_DEFAULTS[agent]
            models = [primary, "opencode/beam-free"]
        else:
            r = classes[cls]
            primary = r["primary"]
            models = [{"model": primary, "reasoning": REASONING.get(agent, "medium")}]
            for fb in r["fallbacks"]:
                models.append(fb)
        agents[agent] = {"models": models}

    categories = {}
    for cat, cls in CATEGORY_MAP.items():
        if cls is None:
            categories[cat] = {"model": FREE_CAT[cat]}
        else:
            r = classes[cls]
            categories[cat] = {"model": r["primary"], "reasoning": r["variant"]} if r["variant"] else {"model": r["primary"]}

    return {
        "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
        "[opencode]": {
            "agents": agents,
            "categories": categories,
            "model_fallback": True,
        },
        "_migrations": schema.get("_migrations", []),
    }


def main():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    out = build(schema)
    # JSONC: write with leading comment
    text = "// OMO configuration — GENERATED from routing.schema.json (do not hand-edit)\n"
    text += json.dumps(out, indent=2) + "\n"
    OMO.parent.mkdir(parents=True, exist_ok=True)
    OMO.write_text(text, encoding="utf-8")
    print(f"Wrote {OMO}")
    print("Restart OpenCode/OmO to pick up changes.")


if __name__ == "__main__":
    main()
