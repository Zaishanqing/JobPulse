"""Run the currently automatable JD extraction gap experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from src.gap_experiments import (  # noqa: E402
    build_blinded_annotation_pack,
    build_integrity_rejection_benchmark,
    build_stratified_coverage_report,
    evaluate_independent_annotations,
    evaluate_predictions_against_adjudicated_gold,
    render_integrity_rejection_report,
    render_stratified_coverage_report,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )


def _annotation_protocol(sample_count: int) -> str:
    return f"""# JD 独立双人 Gold 标注协议

- 样本数：{sample_count}
- 两位标注者必须独立完成，不能查看模型预测、既有 AI Gold 或另一位标注结果。
- `publication_decision` 只能填写 `publish` 或 `reject`。
- 每条 requirement 使用标注者本地 ID，并填写 `kind`、`modality` 及原文 Evidence。
- 按 `services/jd-extraction/docs/annotation-standard.md` 填写该 kind 的全部结构化字段；
  不能只标 span 而省略技能、年限、学历等语义值。
- Evidence 的 `quote` 必须严格等于 `jd_text[start:end]`。
- 不得因为某要求罕见而删除原始证据；罕见性属于下游市场校准，不属于抽取 Gold。
- 双人完成后先运行一致性评测，再由第三人只裁决分歧，冻结 adjudicated Gold。

## 门禁

- 双人完成率必须为 100%。
- 发布决策 Cohen's kappa 目标不低于 0.80。
- Requirement exact agreement F1 目标不低于 0.90。
- 未达到门禁时不得对外宣称独立人类 Gold 实验完成。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--span-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--annotation-target", type=int, default=200)
    parser.add_argument("--annotator-a", type=Path)
    parser.add_argument("--annotator-b", type=Path)
    parser.add_argument("--adjudicated-gold", type=Path)
    args = parser.parse_args(argv)

    manifest = _load(args.manifest)
    span_report = _load(args.span_report)
    args.out.mkdir(parents=True, exist_ok=True)

    coverage = build_stratified_coverage_report(manifest, span_report)
    _write_json(args.out / "stratified-generalization.json", coverage)
    (args.out / "stratified-generalization.md").write_text(
        render_stratified_coverage_report(coverage), encoding="utf-8"
    )

    rejection = build_integrity_rejection_benchmark(manifest)
    _write_json(args.out / "integrity-rejection.json", rejection)
    (args.out / "integrity-rejection.md").write_text(
        render_integrity_rejection_report(rejection), encoding="utf-8"
    )

    pack = build_blinded_annotation_pack(manifest, target=args.annotation_target)
    _write_jsonl(args.out / "annotation-pack.annotator-a.jsonl", pack)
    _write_jsonl(args.out / "annotation-pack.annotator-b.jsonl", pack)
    (args.out / "annotation-protocol.md").write_text(
        _annotation_protocol(len(pack)), encoding="utf-8"
    )
    if args.annotator_a and args.annotator_b:
        agreement = evaluate_independent_annotations(
            _read_jsonl(args.annotator_a), _read_jsonl(args.annotator_b)
        )
    else:
        agreement = evaluate_independent_annotations(pack, pack)
    _write_json(args.out / "human-gold-status.json", agreement)
    human_gold_evaluation = evaluate_predictions_against_adjudicated_gold(
        manifest,
        _read_jsonl(args.adjudicated_gold) if args.adjudicated_gold else pack,
    )
    _write_json(args.out / "human-gold-evaluation.json", human_gold_evaluation)

    summary = {
        "schema_version": "jd-gap-experiments.v1",
        "dataset_version": manifest.get("dataset_version"),
        "automated_experiments": {
            "stratified_generalization": {
                "status": "complete",
                "gate_passed": coverage["gate_passed"],
                "coverage_gate_passed": coverage["coverage_gate_passed"],
                "challenge_coverage_gate_passed": coverage[
                    "challenge_coverage_gate_passed"
                ],
                "performance_gate_passed": coverage["performance_gate_passed"],
                "overall": {
                    "exact_span_f1_micro": coverage["overall"][
                        "exact_span_f1_micro"
                    ],
                    "exact_span_f1_jd_macro": coverage["overall"][
                        "exact_span_f1_jd_macro"
                    ],
                    "case_exact_match_rate": coverage["overall"][
                        "case_exact_match_rate"
                    ],
                    "error_case_count": coverage["overall"]["error_case_count"],
                },
            },
            "integrity_rejection": {
                "status": "complete",
                "accuracy": rejection["accuracy"],
                "scope": rejection["scope"],
            },
        },
        "human_gold": agreement,
        "human_gold_evaluation": human_gold_evaluation,
        "claim_boundary": (
            "Automated results do not replace independent human semantic gold or "
            "real semantic rejection cases."
        ),
    }
    _write_json(args.out / "summary.json", summary)
    print(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
