from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REVIEW_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "review_rules.yaml"


@lru_cache(maxsize=1)
def load_review_rules() -> dict[str, Any]:
    payload = yaml.safe_load(REVIEW_RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review_rules.yaml must contain an object")
    for key in ("hard_errors", "soft_review_flags"):
        if not isinstance(payload.get(key), list):
            raise ValueError(f"review_rules.yaml must contain a {key} list")
    return payload


def get_review_rule(issue_type: str) -> dict[str, str]:
    for rule in load_review_rules()["soft_review_flags"]:
        if rule["issue_type"] == issue_type:
            return rule
    raise KeyError(issue_type)


def get_soft_review_issue_types() -> set[str]:
    return {rule["issue_type"] for rule in load_review_rules()["soft_review_flags"]}


def get_hard_error_types() -> set[str]:
    return {rule["error_type"] for rule in load_review_rules()["hard_errors"]}
