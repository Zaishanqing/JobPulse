from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.deepseek_client import DeepSeekClient  # noqa: E402
from src.report_generator import (  # noqa: E402
    REPORT_GENERATOR_VERSION,
    generate_run_report,
)
from src.review_rules import get_review_rule  # noqa: E402


SYSTEM_PROMPT = """你是 position-taxonomy.v3 岗位多维分类审查员。

先判断是否属于目录范围，再在全部 standard_positions 中比较 Top-K，不得先锁定岗位族，也不得强制选择岗位。

判定原则：
1. 岗位身份只由核心职责和交付物决定；职级、管理范围、技术方向和行业独立输出。
2. classification_status 只能是 resolved、ambiguous、out_of_scope、catalog_gap。
3. resolved 必须同时满足充分 Evidence、Top-1 分数达到阈值且 Top-1/Top-2 差值充分。
4. 多个合理候选接近时输出 ambiguous；范围内无合适岗位输出 catalog_gap；范围外输出 out_of_scope。
5. position_code 仅在 resolved 时必填；其他状态必须为 null。candidate_positions 保留最多 3 个目录内候选及分数。
6. career_level、leadership_scope、technology_focus_codes、industry_context_codes 必须使用给定枚举。
7. observed_skill_domain_codes 只能来自输入 JD 的 skill_domains，不得复制岗位族允许领域。
8. evidence_refs 引用输入 responsibilities 的 evidence_ref；没有完整证据不得 resolved。

只输出紧凑JSON对象：
{"decisions":[{"document_id":str,"classification_status":str,"position_code":str|null,"candidate_positions":[{"position_code":str,"score":number}],"career_level":str,"leadership_scope":str,"technology_focus_codes":list[str],"industry_context_codes":list[str],"observed_skill_domain_codes":list[str],"confidence":number,"review_reason_codes":list[str],"evidence_refs":list[str]}]}

confidence 和候选 score 范围0到1。不要输出Markdown、解释或额外字段。"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_provider_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() and value.strip():
            values[name.strip()] = value.strip()
    return values


def _load_api_key(env_path: Path) -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL")
    if api_key and base_url:
        return
    values = _load_provider_env(env_path)
    if not api_key:
        api_key = values.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError(f"DEEPSEEK_API_KEY is missing from {env_path}")
        os.environ["DEEPSEEK_API_KEY"] = api_key
    if not base_url:
        base_url = values.get("DEEPSEEK_BASE_URL")
        if base_url:
            os.environ["DEEPSEEK_BASE_URL"] = base_url


def _validate_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != "position-taxonomy-catalog.v3":
        raise ValueError("catalog schema must be position-taxonomy-catalog.v3")
    families = payload.get("families")
    positions = payload.get("positions")
    if not isinstance(families, list) or not isinstance(positions, list):
        raise ValueError("catalog families and positions must be lists")
    family_by_code: dict[str, dict[str, Any]] = {}
    for family in families:
        code = family.get("code") if isinstance(family, dict) else None
        domains = family.get("allowed_skill_domains") if isinstance(family, dict) else None
        if not isinstance(code, str) or not code or not isinstance(domains, list) or not domains:
            raise ValueError(f"invalid position family: {family!r}")
        if code in family_by_code:
            raise ValueError(f"duplicate position family: {code}")
        family_by_code[code] = family
    position_by_code: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for position in positions:
        if not isinstance(position, dict):
            raise ValueError("position entry must be an object")
        code = position.get("code")
        name = position.get("name")
        family_code = position.get("family_code")
        if not isinstance(code, str) or not code or not isinstance(name, str) or not name:
            raise ValueError(f"invalid standard position: {position!r}")
        if family_code not in family_by_code:
            raise ValueError(f"position {code} references missing family {family_code}")
        if code in position_by_code or name in names:
            raise ValueError(f"duplicate standard position: {code}")
        position_by_code[code] = position
        names.add(name)
    for field in (
        "career_levels",
        "leadership_scopes",
        "technology_focus_codes",
        "industry_context_codes",
    ):
        if not isinstance(payload.get(field), list):
            raise ValueError(f"catalog {field} must be a list")
    return {
        **payload,
        "_family_by_code": family_by_code,
        "_position_by_code": position_by_code,
    }


def _responsibility_text(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("action", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    evidence = item.get("evidence")
    quote = evidence.get("quote") if isinstance(evidence, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() else None


def _profile(extraction: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    title = (extraction.get("job_title") or {}).get("value")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"job title missing: {extraction.get('document_id')}")
    responsibilities = []
    for item in extraction.get("responsibilities", []):
        text = _responsibility_text(item)
        if not text:
            continue
        evidence = item.get("evidence") if isinstance(item, dict) else None
        evidence_ref = (
            str(item.get("requirement_id"))
            if isinstance(item, dict) and item.get("requirement_id")
            else (
                str(evidence.get("source_id"))
                if isinstance(evidence, dict) and evidence.get("source_id")
                else None
            )
        )
        responsibilities.append({"text": text, "evidence_ref": evidence_ref})
    responsibilities = responsibilities[:8]
    skills: list[str] = []
    domain_counts: Counter[str] = Counter()
    for requirement in normalized.get("normalized_requirements", []):
        if not isinstance(requirement, dict):
            continue
        for skill in requirement.get("skills", []):
            if not isinstance(skill, dict):
                continue
            name = skill.get("canonical_name") or skill.get("source_name")
            if isinstance(name, str) and name.strip() and name not in skills:
                skills.append(name.strip())
            for relation in skill.get("classifications", []):
                if isinstance(relation, dict) and relation.get("facet") == "domain":
                    code = relation.get("code")
                    if isinstance(code, str):
                        domain_counts[code] += 1
    return {
        "document_id": str(extraction["document_id"]),
        "title": title.strip(),
        "responsibilities": responsibilities,
        "skills": skills[:24],
        "skill_domains": [code for code, _ in domain_counts.most_common(8)],
        "available_evidence_refs": [
            item["evidence_ref"]
            for item in responsibilities
            if item["evidence_ref"] is not None
        ],
    }


def _selected_runs(args: argparse.Namespace) -> list[Path]:
    if args.run:
        runs = args.run
    else:
        runs = sorted(path for path in args.runs_root.iterdir() if path.is_dir())
    if not runs:
        raise ValueError("no JD runs selected")
    return runs


def _load_records(run_dirs: list[Path]) -> tuple[list[dict[str, Any]], dict[str, tuple[Path, int]]]:
    profiles: list[dict[str, Any]] = []
    locations: dict[str, tuple[Path, int]] = {}
    for run in run_dirs:
        final_dir = run / "final"
        annotations_path = final_dir / "annotations_nested.json"
        normalized_path = final_dir / "normalized_annotations.json"
        if not annotations_path.is_file() or not normalized_path.is_file():
            raise ValueError(f"run lacks final extraction outputs: {run}")
        annotations = _read_json(annotations_path)
        normalized = _read_json(normalized_path)
        if not isinstance(annotations, list) or not isinstance(normalized, list):
            raise ValueError(f"run outputs must be lists: {run}")
        normalized_by_id = {str(item["document_id"]): item for item in normalized}
        annotation_ids = {str(item["document_id"]) for item in annotations}
        if annotation_ids != set(normalized_by_id):
            raise ValueError(f"document sets differ: {run}")
        for index, extraction in enumerate(annotations):
            document_id = str(extraction["document_id"])
            if document_id in locations:
                raise ValueError(f"duplicate document_id across runs: {document_id}")
            profiles.append(_profile(extraction, normalized_by_id[document_id]))
            locations[document_id] = (run, index)
    return profiles, locations


def _compact_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    families = catalog["_family_by_code"]
    return [
        {
            "code": item["code"],
            "name": item["name"],
            "family_code": item["family_code"],
            "family_name": families[item["family_code"]]["name"],
            "definition": item["definition"],
            "skill_domains": families[item["family_code"]]["allowed_skill_domains"],
        }
        for item in catalog["positions"]
    ]


def _request_batch(
    batch: list[dict[str, Any]],
    catalog: dict[str, Any],
    model: str,
    validation_failure: str | None = None,
) -> list[dict[str, Any]]:
    payload = {
        "standard_positions": _compact_catalog(catalog),
        "career_levels": catalog["career_levels"],
        "leadership_scopes": catalog["leadership_scopes"],
        "technology_focus_codes": catalog["technology_focus_codes"],
        "industry_context_codes": catalog["industry_context_codes"],
        "jds": batch,
    }
    if validation_failure:
        payload["previous_response_validation_failure"] = validation_failure
        payload["correction_required"] = "重新输出整个批次，并严格使用给定枚举值"
    response = DeepSeekClient(model=model, timeout=240).extract(
        SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ).data
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("DeepSeek response has no decisions list")
    return decisions


def _request_validated_batch(
    batch: list[dict[str, Any]],
    catalog: dict[str, Any],
    model: str,
    max_attempts: int,
) -> dict[str, dict[str, Any]]:
    expected = {item["document_id"] for item in batch}
    validation_failure: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            raw = _request_batch(batch, catalog, model, validation_failure)
            return _validate_decisions(
                raw,
                expected,
                catalog,
                profiles_by_id={item["document_id"]: item for item in batch},
            )
        except ValueError as exc:
            validation_failure = str(exc)
            if attempt == max_attempts:
                raise
    raise AssertionError("unreachable")


def _validate_decisions(
    raw: list[dict[str, Any]],
    expected_ids: set[str],
    catalog: dict[str, Any],
    *,
    profiles_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    position_codes = set(catalog["_position_by_code"])
    career_levels = set(catalog["career_levels"])
    leadership_scopes = set(catalog["leadership_scopes"])
    technology_codes = set(catalog["technology_focus_codes"])
    industry_codes = set(catalog["industry_context_codes"])
    statuses = {"resolved", "ambiguous", "out_of_scope", "catalog_gap"}
    policy = catalog.get("classification_policy") or {}
    min_score = float(policy.get("resolved_min_score", 0.75))
    min_margin = float(policy.get("resolved_min_margin", 0.08))
    min_evidence_refs = int(policy.get("resolved_min_evidence_refs", 1))
    decisions: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("decision must be an object")
        document_id = item.get("document_id")
        status = item.get("classification_status")
        position_code = item.get("position_code")
        candidates = item.get("candidate_positions")
        career_level = item.get("career_level")
        leadership_scope = item.get("leadership_scope")
        technologies = item.get("technology_focus_codes")
        industries = item.get("industry_context_codes")
        observed_domains = item.get("observed_skill_domain_codes")
        confidence = item.get("confidence")
        review_reasons = item.get("review_reason_codes")
        evidence_refs = item.get("evidence_refs")
        if document_id not in expected_ids or document_id in decisions:
            raise ValueError(f"unexpected or duplicate decision: {document_id}")
        if status not in statuses:
            raise ValueError(f"invalid classification_status for {document_id}: {status}")
        if status == "resolved" and position_code not in position_codes:
            raise ValueError(f"unknown position_code for {document_id}: {position_code}")
        if status != "resolved" and position_code is not None:
            raise ValueError(f"unresolved decision must not bind position_code: {document_id}")
        if not isinstance(candidates, list) or len(candidates) > 3:
            raise ValueError(f"invalid candidate_positions for {document_id}")
        for candidate in candidates:
            if (
                not isinstance(candidate, dict)
                or candidate.get("position_code") not in position_codes
                or not isinstance(candidate.get("score"), (int, float))
                or isinstance(candidate.get("score"), bool)
                or not 0 <= candidate["score"] <= 1
            ):
                raise ValueError(f"invalid candidate for {document_id}")
        if career_level not in career_levels or leadership_scope not in leadership_scopes:
            raise ValueError(f"invalid level or leadership scope for {document_id}")
        if not isinstance(technologies, list) or any(code not in technology_codes for code in technologies):
            raise ValueError(f"invalid technology_focus_codes for {document_id}")
        if not isinstance(industries, list) or any(code not in industry_codes for code in industries):
            raise ValueError(f"invalid industry_context_codes for {document_id}")
        if not isinstance(observed_domains, list):
            raise ValueError(f"invalid observed_skill_domain_codes for {document_id}")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise ValueError(f"invalid confidence for {document_id}: {confidence}")
        if not isinstance(review_reasons, list) or not isinstance(evidence_refs, list):
            raise ValueError(f"incomplete decision for {document_id}")
        profile = (profiles_by_id or {}).get(document_id)
        if profile is not None:
            if not set(observed_domains).issubset(
                set(profile.get("skill_domains") or [])
            ):
                raise ValueError(
                    f"observed skill domains exceed input evidence for {document_id}"
                )
            if not set(evidence_refs).issubset(
                set(profile.get("available_evidence_refs") or [])
            ):
                raise ValueError(
                    f"classification evidence refs exceed input evidence for {document_id}"
                )
        if status == "resolved":
            sorted_candidates = sorted(
                candidates,
                key=lambda candidate: float(candidate["score"]),
                reverse=True,
            )
            if (
                not sorted_candidates
                or sorted_candidates[0]["position_code"] != position_code
                or float(sorted_candidates[0]["score"]) < min_score
                or len(evidence_refs) < min_evidence_refs
            ):
                raise ValueError(
                    f"resolved decision does not meet score/evidence policy: {document_id}"
                )
            if (
                len(sorted_candidates) > 1
                and float(sorted_candidates[0]["score"])
                - float(sorted_candidates[1]["score"])
                < min_margin
            ):
                raise ValueError(
                    f"resolved candidate margin is too small: {document_id}"
                )
        decisions[document_id] = {
            "document_id": document_id,
            "classification_status": status,
            "position_code": position_code,
            "candidate_positions": candidates,
            "career_level": career_level,
            "leadership_scope": leadership_scope,
            "technology_focus_codes": technologies,
            "industry_context_codes": industries,
            "observed_skill_domain_codes": observed_domains,
            "confidence": round(float(confidence), 4),
            "review_reason_codes": review_reasons,
            "evidence_refs": evidence_refs,
        }
    if set(decisions) != expected_ids:
        raise ValueError(f"batch decision set differs: missing={sorted(expected_ids-set(decisions))}")
    return decisions


def _checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), dict):
        raise ValueError(f"invalid checkpoint: {path}")
    return payload["decisions"]


def classify(args: argparse.Namespace) -> None:
    _load_api_key(args.env_file)
    catalog = _validate_catalog(_read_json(args.catalog))
    profiles, _ = _load_records(_selected_runs(args))
    profiles_by_id = {item["document_id"]: item for item in profiles}
    decisions = _checkpoint(args.checkpoint)
    unexpected = set(decisions) - set(profiles_by_id)
    if unexpected:
        raise ValueError(f"checkpoint contains unknown documents: {sorted(unexpected)}")
    pending = [item for item in profiles if item["document_id"] not in decisions]
    if args.limit_documents is not None:
        pending = pending[: args.limit_documents]
    batches = [pending[index : index + args.batch_size] for index in range(0, len(pending), args.batch_size)]
    if batches:
        worker_count = min(args.max_workers, len(batches))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="position-taxonomy") as executor:
            future_batches = {
                executor.submit(
                    _request_validated_batch,
                    batch,
                    catalog,
                    args.model,
                    args.max_attempts,
                ): batch
                for batch in batches
            }
            for completed, future in enumerate(as_completed(future_batches), 1):
                validated = future.result()
                decisions.update(validated)
                _write_json_atomic(
                    args.checkpoint,
                    {
                        "schema": "position-reclassification-checkpoint.v3",
                        "catalog_version": catalog["catalog_version"],
                        "model": args.model,
                        "decisions": decisions,
                    },
                )
                print(
                    json.dumps(
                        {
                            "completed_batches": completed,
                            "total_batches": len(batches),
                            "classified_documents": len(decisions),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    if args.limit_documents is None and set(decisions) != set(profiles_by_id):
        raise ValueError("classification did not cover every document")
    print(
        json.dumps(
            {
                "documents": len(profiles),
                "classified_documents": len(decisions),
                "positions": len(catalog["positions"]),
                "review_required": sum(
                    1
                    for item in decisions.values()
                    if item["classification_status"] in {"ambiguous", "catalog_gap"}
                ),
                "checkpoint": str(args.checkpoint),
            },
            ensure_ascii=False,
        )
    )


def apply(args: argparse.Namespace) -> None:
    catalog = _validate_catalog(_read_json(args.catalog))
    source_runs = _selected_runs(args)
    profiles, _ = _load_records(source_runs)
    profiles_by_id = {item["document_id"]: item for item in profiles}
    decisions = _checkpoint(args.checkpoint)
    if set(decisions) != set(profiles_by_id):
        raise ValueError("checkpoint must classify every current document before apply")
    decisions = _validate_decisions(
        list(decisions.values()),
        set(profiles_by_id),
        catalog,
        profiles_by_id=profiles_by_id,
    )
    applied_at = datetime.now(timezone.utc).isoformat()
    counts: Counter[str] = Counter()
    review_count = 0
    args.output_runs_root.mkdir(parents=True, exist_ok=True)
    for source_run in source_runs:
        run = args.output_runs_root / f"{source_run.name}_position_v3"
        if run.exists():
            raise ValueError(f"v3 output run already exists: {run}")
        shutil.copytree(source_run, run)
        final_dir = run / "final"
        annotations = _read_json(final_dir / "annotations_nested.json")
        normalized_path = final_dir / "normalized_annotations.json"
        normalized = _read_json(normalized_path)
        annotation_by_id = {str(item["document_id"]): item for item in annotations}
        for row in normalized:
            document_id = str(row["document_id"])
            decision = decisions[document_id]
            position = (
                catalog["_position_by_code"][decision["position_code"]]
                if decision["position_code"] is not None
                else None
            )
            family = (
                catalog["_family_by_code"][position["family_code"]]
                if position is not None
                else None
            )
            source_title = (annotation_by_id[document_id].get("job_title") or {}).get("value")
            row["job_classification"] = {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": catalog["catalog_version"],
                "source_title": source_title,
                "position_code": position["code"] if position else None,
                "position_name": position["name"] if position else None,
                "family_code": family["code"] if family else None,
                "family_name": family["name"] if family else None,
                "candidate_positions": decision["candidate_positions"],
                "career_level": decision["career_level"],
                "leadership_scope": decision["leadership_scope"],
                "technology_focus_codes": decision["technology_focus_codes"],
                "industry_context_codes": decision["industry_context_codes"],
                "observed_skill_domain_codes": decision["observed_skill_domain_codes"],
                "confidence": decision["confidence"],
                "classification_status": decision["classification_status"],
                "review_reason_codes": decision["review_reason_codes"],
                "evidence_refs": decision["evidence_refs"],
                "classification_policy_version": catalog["classification_policy_version"],
            }
            counts[decision["classification_status"]] += 1
            review_count += int(
                decision["classification_status"] in {"ambiguous", "catalog_gap"}
            )
        _write_json_atomic(normalized_path, normalized)
        _write_jsonl_atomic(final_dir / "normalized_annotations.jsonl", normalized)
        manifest_path = run / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest["position_taxonomy_version"] = catalog["catalog_version"]
        manifest["position_reclassified_at"] = applied_at
        manifest["position_reclassification_model"] = args.model
        _write_json_atomic(manifest_path, manifest)
        _refresh_run_metadata_and_report(
            run,
            catalog_version=catalog["catalog_version"],
            model=args.model,
            applied_at=applied_at,
        )
    report = {
        "schema": "position-reclassification-report.v3",
        "catalog_version": catalog["catalog_version"],
        "model": "deepseek-v4-flash",
        "applied_at": applied_at,
        "document_count": len(profiles),
        "classification_status_counts": dict(sorted(counts.items())),
        "review_required_count": review_count,
    }
    _write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False))


def _refresh_run_metadata_and_report(
    run: Path,
    *,
    catalog_version: str,
    model: str,
    applied_at: str,
) -> dict[str, int]:
    final = run / "final"
    normalized = _read_json(final / "normalized_annotations.json")
    counts: Counter[str] = Counter()
    flags_path = final / "review_flags.jsonl"
    flags = [
        json.loads(line)
        for line in flags_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if flags_path.exists() else []
    flags = [
        flag
        for flag in flags
        if flag.get("issue_type") != "job_classification_not_resolved"
    ]
    rule = get_review_rule("job_classification_not_resolved")
    for row in normalized:
        classification = row.get("job_classification") or {}
        status = str(classification.get("classification_status", "missing"))
        counts[status] += 1
        if status not in {"resolved", "manually_confirmed"}:
            flags.append(
                {
                    "jd_id": str(row["document_id"]),
                    "requirement_id": "",
                    "item_id": "",
                    "issue_type": "job_classification_not_resolved",
                    "severity": rule["severity"],
                    "rule_scope": rule["scope"],
                    "issue_description": rule["description"],
                    "raw_text": "岗位分类未达到发布状态",
                    "suggested_action": rule["suggested_action"],
                    "classification_status": status,
                    "position_code": classification.get("position_code"),
                    "review_reason_codes": classification.get(
                        "review_reason_codes", []
                    ),
                }
            )
    _write_jsonl_atomic(flags_path, flags)
    manifest_path = run / "manifest.json"
    manifest = _read_json(manifest_path)
    report_path = run / "research_report.md"
    manifest.update(
        {
            "position_taxonomy_version": catalog_version,
            "position_reclassified_at": applied_at,
            "position_reclassification_model": model,
            "review_flag_count": len(flags),
            "research_report_path": str(report_path),
            "report_generator_version": REPORT_GENERATOR_VERSION,
        }
    )
    _write_json_atomic(manifest_path, manifest)
    generate_run_report(run, report_path=report_path)
    return dict(sorted(counts.items()))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create an independent position-taxonomy.v3 JD reclassification run."
    )
    result.add_argument("command", choices=("classify", "apply"))
    result.add_argument(
        "--runs-root", type=Path, default=PROJECT_ROOT / "output" / "runs"
    )
    result.add_argument(
        "--run",
        action="append",
        type=Path,
        help="Specific source run to classify; repeat for multiple runs.",
    )
    result.add_argument(
        "--output-runs-root",
        type=Path,
        default=PROJECT_ROOT / "output" / "runs_position_v3",
    )
    result.add_argument(
        "--catalog",
        type=Path,
        default=PROJECT_ROOT / "config" / "position_taxonomy_catalog.v3.json",
    )
    result.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
    )
    result.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "output" / "position_reclassification_v3.checkpoint.json",
    )
    result.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "output" / "position_reclassification_v3.report.json",
    )
    result.add_argument("--model", default="deepseek-v4-flash")
    result.add_argument("--batch-size", type=int, default=20)
    result.add_argument("--max-workers", type=int, default=2500)
    result.add_argument("--max-attempts", type=int, default=3)
    result.add_argument("--limit-documents", type=int)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.model != "deepseek-v4-flash":
        raise ValueError("position reclassification model must be deepseek-v4-flash")
    if (
        args.batch_size < 1
        or args.max_workers < 1
        or args.max_workers > 2500
        or args.max_attempts < 1
        or (args.limit_documents is not None and args.limit_documents < 1)
    ):
        raise ValueError("batch-size and max-workers must be within configured limits")
    if args.command == "classify":
        classify(args)
    else:
        apply(args)


if __name__ == "__main__":
    main()


