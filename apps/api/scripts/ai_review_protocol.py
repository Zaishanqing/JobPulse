"""Shared two-round AI review protocol with third-round arbitration.

The protocol intentionally produces ``ai-reviewed`` assets, never ``human_gold``.
Two independent judges review the same frozen pack; disagreements are sent to a
third judge; items that still cannot be confirmed become ``abstain`` and are
excluded from proxy metrics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


def run_review_rounds(
    *,
    review_pack: Mapping[str, object],
    judge: Callable[[str, str], dict[str, object]],
    system_prompt: str,
    user_prompt: str,
    user_prompt_round_b: str | None = None,
    arbitrate: Callable[[str, str], dict[str, object]],
    arbitration_prompt: str,
    resolve: Callable[[dict[str, object], dict[str, object]], tuple[str, object] | None],
    decision_key: str,
    arbiter_decides: bool = False,
) -> dict[str, object]:
    """Run two judge rounds plus arbitration and freeze the AI-reviewed gold.

    ``judge`` receives ``(system_prompt, user_prompt)`` and returns the parsed
    JSON object.  ``resolve`` receives the two per-item judge decisions and
    returns ``(final_label, final_value)`` for agreement, or ``None`` when the
    disagreement must be arbitrated.  The ``arbitrate`` callable receives
    ``(arbitration_prompt, arbitration_user_payload)``.  When
    ``arbiter_decides`` is true the third judge's own decision is the frozen
    final outcome; otherwise the existing confirm-with-judge-a rule applies.
    """
    round_b_prompt = user_prompt_round_b or user_prompt
    first = _normalize_decisions(judge(system_prompt, user_prompt))
    second = _normalize_decisions(judge(system_prompt, round_b_prompt))
    item_ids = list(review_pack["items"].keys())
    disagreements = [
        item_id
        for item_id in item_ids
        if item_id in first
        and item_id in second
        and resolve(first[item_id], second[item_id]) is None
    ]
    arbitration: dict[str, object] = {}
    if disagreements:
        arbitration_payload = {
            "protocol": review_pack.get("protocol"),
            "decision_key": decision_key,
            "disputed_items": [
                {
                    "item_id": item_id,
                    "item": review_pack["items"][item_id],
                    "judge_a": first.get(item_id),
                    "judge_b": second.get(item_id),
                }
                for item_id in disagreements
            ],
        }
        arbitration = _normalize_decisions(
            arbitrate(
                arbitration_prompt,
                json.dumps(arbitration_payload, ensure_ascii=False, indent=2),
            )
        )

    gold_items: dict[str, object] = {}
    for item_id in item_ids:
        decision_a = first.get(item_id)
        decision_b = second.get(item_id)
        if decision_a is None or decision_b is None:
            gold_items[item_id] = _abstain_record(
                item_id,
                decision_a,
                decision_b,
                reason="missing_judge_decision",
            )
            continue
        agreed = resolve(decision_a, decision_b)
        if agreed is not None:
            final_label, final_value = agreed
            gold_items[item_id] = {
                "item_id": item_id,
                "judge_a": decision_a,
                "judge_b": decision_b,
                "arbitrated": False,
                "final_label": final_label,
                "final_value": final_value,
            }
            continue
        arbiter = arbitration.get(item_id)
        if arbiter is None:
            gold_items[item_id] = _abstain_record(
                item_id,
                decision_a,
                decision_b,
                reason="arbitration_missing",
            )
            continue
        if arbiter.get("abstain") is True:
            gold_items[item_id] = _abstain_record(
                item_id,
                decision_a,
                decision_b,
                reason="arbitration_abstain",
                arbiter=arbiter,
            )
            continue
        resolved = (
            resolve(arbiter, arbiter)
            if arbiter_decides
            else resolve(decision_a, arbiter)
        )
        if resolved is None:
            gold_items[item_id] = _abstain_record(
                item_id,
                decision_a,
                decision_b,
                reason="arbitration_unconfirmed",
                arbiter=arbiter,
            )
            continue
        final_label, final_value = resolved
        gold_items[item_id] = {
            "item_id": item_id,
            "judge_a": decision_a,
            "judge_b": decision_b,
            "arbiter": arbiter,
            "arbitrated": True,
            "final_label": final_label,
            "final_value": final_value,
        }

    decided = [
        item for item in gold_items.values() if item["final_label"] != "abstain"
    ]
    abstained = [
        item for item in gold_items.values() if item["final_label"] == "abstain"
    ]
    agreement = sum(
        1
        for item_id in item_ids
        if item_id in first
        and item_id in second
        and resolve(first[item_id], second[item_id]) is not None
    )
    frozen_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "ai-review-protocol.v1",
        "protocol": review_pack.get("protocol"),
        "decision_key": decision_key,
        "item_count": len(item_ids),
        "judge_a_count": len(first),
        "judge_b_count": len(second),
        "agreement_count": agreement,
        "disagreement_count": len(disagreements),
        "arbitration_requested": len(disagreements),
        "arbitration_resolved": sum(
            1 for item in gold_items.values() if item.get("arbitrated")
        ),
        "arbitration_mode": (
            "arbiter_decides" if arbiter_decides else "confirm_judge_a"
        ),
        "abstain_count": len(abstained),
        "decided_count": len(decided),
        "judge_agreement_rate": (
            round(agreement / len(item_ids), 6) if item_ids else None
        ),
        "items": gold_items,
        "frozen_at": frozen_at,
        "is_human_gold": False,
        "gold_note": (
            "AI-reviewed proxy; not human gold; abstain excluded from metrics"
        ),
    }


def _abstain_record(
    item_id: str,
    decision_a: Mapping[str, object] | None,
    decision_b: Mapping[str, object] | None,
    *,
    reason: str,
    arbiter: Mapping[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "item_id": item_id,
        "judge_a": decision_a,
        "judge_b": decision_b,
        "arbitrated": arbiter is not None,
        "final_label": "abstain",
        "final_value": None,
        "abstain_reason": reason,
    }
    if arbiter is not None:
        record["arbiter"] = arbiter
    return record


def _normalize_decisions(decisions: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(decisions, dict):
        return {}
    normalized: dict[str, object] = {}
    for key, value in decisions.items():
        if isinstance(key, str) and isinstance(value, dict):
            normalized[key] = value
    return normalized


def write_frozen_gold(
    output_dir: Path,
    filename: str,
    gold: Mapping[str, object],
    *,
    experiment_id: str,
    gold_version: str,
    source_files: Sequence[str],
    review_pack: Mapping[str, object] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ai-reviewed-gold.v1",
        "experiment_id": experiment_id,
        "gold_version": gold_version,
        "source_files": list(source_files),
        "pack": review_pack,
        "review": gold,
    }
    path = output_dir / filename
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_frozen_gold(path: Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
