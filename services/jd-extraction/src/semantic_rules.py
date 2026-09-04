from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


SEMANTIC_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "model_semantic_rules.yaml"


@lru_cache(maxsize=1)
def load_semantic_rules() -> dict:
    payload = yaml.safe_load(SEMANTIC_RULES_PATH.read_text(encoding="utf-8"))
    required = {
        "scope_boundaries",
        "requirement_kinds",
        "modality",
        "evidence_rules",
        "skill_item_types",
        "atomicity_rules",
        "normalization_boundary",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError(f"model_semantic_rules.yaml must contain: {sorted(required)}")
    return payload


def compile_semantic_handbook() -> str:
    rules = load_semantic_rules()
    lines = [f"语义规则版本：{rules.get('version', '未版本化')}"]
    for title, key in (
        ("结果边界", "scope_boundaries"),
        ("requirement kind", "requirement_kinds"),
        ("modality", "modality"),
        ("skill item_type", "skill_item_types"),
    ):
        lines.append(f"## {title}")
        lines.extend(f"- {name}: {value}" for name, value in rules[key].items())
    for title, key in (
        ("Evidence", "evidence_rules"),
        ("原子性", "atomicity_rules"),
        ("细粒度边界", "detailed_boundaries"),
        ("归一化边界", "normalization_boundary"),
    ):
        lines.append(f"## {title}")
        lines.extend(f"- {value}" for value in rules[key])
    return "\n".join(lines)
