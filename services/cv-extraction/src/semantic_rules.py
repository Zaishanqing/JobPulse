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
        "evidence_rules",
        "skill_item_types",
        "atomicity_rules",
        "normalization_boundary",
        "deterministic_validation",
        "repair_instructions",
        "source_coverage",
        "proficiency_evidence",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError(f"model_semantic_rules.yaml must contain: {sorted(required)}")
    return payload


def deterministic_validation_rules() -> dict:
    rules = load_semantic_rules()["deterministic_validation"]
    if not isinstance(rules, dict):
        raise ValueError("deterministic_validation must be an object")
    return rules


def repair_instruction(code: str) -> str | None:
    instructions = load_semantic_rules()["repair_instructions"]
    if not isinstance(instructions, dict):
        raise ValueError("repair_instructions must be an object")
    value = instructions.get(code)
    return value if isinstance(value, str) and value.strip() else None


def source_coverage_rules() -> dict:
    rules = load_semantic_rules()["source_coverage"]
    if not isinstance(rules, dict):
        raise ValueError("source_coverage must be an object")
    return rules


def proficiency_evidence_rules() -> dict:
    rules = load_semantic_rules()["proficiency_evidence"]
    if not isinstance(rules, dict):
        raise ValueError("proficiency_evidence must be an object")
    return rules


def language_proficiency_evidence_rules() -> dict:
    rules = load_semantic_rules()["language_proficiency_evidence"]
    if not isinstance(rules, dict):
        raise ValueError("language_proficiency_evidence must be an object")
    return rules


@lru_cache(maxsize=8)
def compile_semantic_handbook(
    top_level_fields: tuple[str, ...] | None = None,
) -> str:
    rules = load_semantic_rules()
    selected_fields = set(top_level_fields) if top_level_fields is not None else None
    requirement_fields = {
        "education": "education",
        "work": "work_experience",
        "project": "project_experience",
        "skill": "skills",
        "language": "languages",
        "certificate": "certificates",
        "award": "awards",
        "self_eval": "self_evaluation",
    }
    skill_bearing_fields = {"skills", "work_experience", "project_experience"}
    lines = [f"语义规则版本：{rules.get('version', '未版本化')}"]
    for title, key in (
        ("结果边界", "scope_boundaries"),
        ("分类体系", "requirement_kinds"),
        ("skill item_type", "skill_item_types"),
    ):
        if key in rules and rules[key] is not None:
            if (
                selected_fields is not None
                and key == "skill_item_types"
                and not selected_fields.intersection(skill_bearing_fields)
            ):
                continue
            lines.append(f"## {title}")
            if isinstance(rules[key], dict):
                items = rules[key].items()
                if selected_fields is not None and key == "scope_boundaries":
                    items = (
                        (name, value)
                        for name, value in items
                        if name in selected_fields
                    )
                elif selected_fields is not None and key == "requirement_kinds":
                    items = (
                        (name, value)
                        for name, value in items
                        if requirement_fields.get(name) in selected_fields
                    )
                lines.extend(f"- {name}: {value}" for name, value in items)
            elif isinstance(rules[key], list):
                lines.extend(f"- {value}" for value in rules[key])
    for title, key in (
        ("Evidence", "evidence_rules"),
        ("原子性", "atomicity_rules"),
        ("细粒度边界", "detailed_boundaries"),
        ("归一化边界", "normalization_boundary"),
    ):
        if key in rules:
            if selected_fields is not None and key == "detailed_boundaries":
                # The annotation standard below carries the relevant section
                # boundaries; repeating all unrelated examples slows every shard.
                continue
            if (
                selected_fields is not None
                and key == "normalization_boundary"
                and not selected_fields.intersection(skill_bearing_fields)
            ):
                continue
            lines.append(f"## {title}")
            items = rules[key]
            if isinstance(items, list):
                lines.extend(f"- {value}" for value in items)
    return "\n".join(lines)
