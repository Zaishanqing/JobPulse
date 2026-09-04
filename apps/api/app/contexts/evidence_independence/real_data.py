from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence, TypeAlias

from app.contexts.evidence_independence.application import text_fingerprint
from app.contexts.evidence_independence.contracts import JsonPayload


TARGET_POSITION_CODES = ("BACKEND_ENGINEER", "LLM_ALGORITHM_ENGINEER")
FREEZE_ALGORITHM_VERSION = "exp-evid-freezer.v1"
TEMPLATE_CANDIDATE_VERSION = "template-candidate.exact-structure.v1"

_SPACE = re.compile(r"\s+")
_DIGIT = re.compile(r"\d+")

InventoryPayload: TypeAlias = dict[str, JsonPayload]
OverlapRow: TypeAlias = dict[str, JsonPayload]
ExclusionRow: TypeAlias = dict[str, JsonPayload]
FreezeManifest: TypeAlias = dict[str, JsonPayload]
SampleRow: TypeAlias = dict[str, JsonPayload]
CrosswalkRow: TypeAlias = dict[str, JsonPayload]
TemplateRow: TypeAlias = dict[str, JsonPayload]
AnnotationRow: TypeAlias = dict[str, JsonPayload]
FreezeConfig: TypeAlias = dict[str, JsonPayload]


@dataclass(frozen=True)
class RealJDCandidate:
    asset_pool: str
    record_identity: str
    document_id: str
    position_code: str | None
    classification_status: str | None
    source_platform: str | None
    source_record_id: str | None
    source_version: str | None
    content_hash: str | None
    source_fact_id: str | None
    source_jd_id: str | None
    enterprise_name: str | None
    enterprise_id: str | None
    published_at: str | None
    observed_at: str | None
    time_basis: str
    title: str
    responsibilities: tuple[str, ...]
    skills: tuple[str, ...]
    text_excerpt: str
    text_fingerprint: str
    release_ids: tuple[str, ...]
    identity_kind: str
    input_ref: str
    crawl_time: str | None = None
    collection_time_basis: str = "unknown"


def load_bundle_candidates(bundle_dir: Path) -> tuple[RealJDCandidate, ...]:
    final_dir = bundle_dir / "final"
    annotations = _jsonl_by_document(final_dir / "annotations.jsonl")
    normalized = _jsonl_by_document(final_dir / "normalized_annotations.jsonl")
    selected = _selected_bundle_rows(bundle_dir / "duplicate_mapping.csv")
    source_mapping = _mapping_rows(
        bundle_dir.parents[1]
        / "data"
        / "prepared_bundles"
        / "bundle_input_mapping_combined.csv"
    )
    records: list[RealJDCandidate] = []
    for document_id in sorted(selected):
        annotation = annotations.get(document_id, {})
        normalized_annotation = normalized.get(document_id, {})
        metadata = {**selected[document_id], **source_mapping.get(document_id, {})}
        records.append(
            _candidate(
                asset_pool="bundles_all_unique_v3",
                document_id=document_id,
                annotation=annotation,
                normalized=normalized_annotation,
                metadata=metadata,
                record_identity=_source_identity(metadata),
                identity_kind="source_identity",
                input_ref=str(bundle_dir),
            )
        )
    return tuple(records)


def load_run_candidates(runs_dir: Path) -> tuple[RealJDCandidate, ...]:
    records: list[RealJDCandidate] = []
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        manifest_path = run_dir / "manifest.json"
        annotation_path = run_dir / "final" / "annotations.jsonl"
        normalized_path = run_dir / "final" / "normalized_annotations.jsonl"
        if not (manifest_path.is_file() and annotation_path.is_file() and normalized_path.is_file()):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        annotations = _jsonl_by_document(annotation_path)
        normalized = _jsonl_by_document(normalized_path)
        row_by_document = _run_rows(run_dir / "records" / "success")
        for document_id in sorted(set(annotations) | set(normalized) | set(row_by_document)):
            row_index = row_by_document.get(document_id)
            if row_index is None:
                continue
            run_id = str(manifest.get("run_id") or run_dir.name)
            metadata = {
                "source_platform": manifest.get("source_platform"),
                # TEMP-LAG-01: only a real crawler envelope acquisition time may
                # be treated as crawler provenance; the pipeline scheduler's
                # ``run_started_at`` is pipeline bookkeeping, never a crawl.  It
                # survives as the `observed_at` lower bound (pipeline_observed).
                "crawl_time": manifest.get("crawl_time"),
                "observed_at": (
                    manifest.get("observed_at") or manifest.get("run_started_at")
                ),
                "run_id": run_id,
                "row_index": row_index,
            }
            records.append(
                _candidate(
                    asset_pool="runs_position_v3",
                    document_id=document_id,
                    annotation=annotations[document_id],
                    normalized=normalized[document_id],
                    metadata=metadata,
                    record_identity=f"run:{run_id}:row:{row_index}:document:{document_id}",
                    identity_kind="run_row_identity",
                    input_ref=str(run_dir),
                )
            )
    return tuple(records)


def build_inventory(
    candidates: Sequence[RealJDCandidate],
) -> tuple[InventoryPayload, list[OverlapRow], list[ExclusionRow]]:
    by_pool = Counter(item.asset_pool for item in candidates)
    target_hits = Counter(
        item.position_code for item in candidates if item.position_code in TARGET_POSITION_CODES
    )
    identity_groups: dict[str, list[RealJDCandidate]] = defaultdict(list)
    for item in candidates:
        identity_groups[item.record_identity].append(item)
    overlaps = [
        {
            "record_identity": identity,
            "asset_pools": sorted({item.asset_pool for item in rows}),
            "document_ids": sorted({item.document_id for item in rows}),
            "record_count": len(rows),
        }
        for identity, rows in sorted(identity_groups.items())
        if len({item.asset_pool for item in rows}) > 1
    ]
    exclusions = [
        {
            "record_identity": item.record_identity,
            "asset_pool": item.asset_pool,
            "document_id": item.document_id,
            "reason": _exclusion_reason(item),
        }
        for item in candidates
        if _exclusion_reason(item) is not None
    ]
    total = len(candidates)
    inventory = {
        "schema_version": "exp-evid-data-inventory.v1",
        "asset_record_count": total,
        "record_count_by_pool": dict(sorted(by_pool.items())),
        "target_position_hits": dict(sorted(target_hits.items())),
        "coverage": {
            "source_platform": _coverage(candidates, "source_platform"),
            "enterprise": _coverage_any(candidates, ("enterprise_id", "enterprise_name")),
            "published_time": _coverage(candidates, "published_at"),
            "observed_time": _coverage(candidates, "observed_at"),
            "source_identity": round(
                sum(item.identity_kind == "source_identity" for item in candidates) / total, 4
            ) if total else 0.0,
            "source_jd_id": _coverage(candidates, "source_jd_id"),
            "source_fact_id": _coverage(candidates, "source_fact_id"),
            "source_version": _coverage(candidates, "source_version"),
            "release_membership": round(
                sum(bool(item.release_ids) for item in candidates) / total, 4
            ) if total else 0.0,
            "text": round(sum(bool(item.text_excerpt) for item in candidates) / total, 4)
            if total else 0.0,
            "responsibilities": round(
                sum(bool(item.responsibilities) for item in candidates) / total, 4
            ) if total else 0.0,
            "skills": round(sum(bool(item.skills) for item in candidates) / total, 4)
            if total else 0.0,
        },
        "identity_overlap_count": len(overlaps),
        "exclusion_count": len(exclusions),
        "identity_policy": {
            "source": "(source_platform, source_record_id, content_hash)",
            "run": "(run_id, row_index, document_id)",
            "fuzzy_cross_pool_merge": False,
        },
    }
    return inventory, overlaps, exclusions


def freeze_target_samples(
    candidates: Sequence[RealJDCandidate],
    *,
    config: FreezeConfig,
) -> tuple[
    FreezeManifest,
    list[SampleRow],
    list[CrosswalkRow],
    list[TemplateRow],
    list[AnnotationRow],
    list[ExclusionRow],
]:
    selected_by_identity: dict[str, RealJDCandidate] = {}
    exclusions: list[dict[str, object]] = []
    for item in sorted(candidates, key=lambda row: (row.record_identity, row.asset_pool)):
        reason = _exclusion_reason(item)
        if reason is not None:
            exclusions.append(_exclusion_row(item, reason))
            continue
        if item.record_identity in selected_by_identity:
            exclusions.append(_exclusion_row(item, "duplicate_explicit_identity"))
            continue
        selected_by_identity[item.record_identity] = item
    selected = list(selected_by_identity.values())
    sample_rows: list[dict[str, object]] = []
    crosswalk: list[dict[str, object]] = []
    templates: list[dict[str, object]] = []
    annotations: list[dict[str, object]] = []
    for item in sorted(selected, key=lambda row: (str(row.position_code), row.record_identity)):
        enterprise_identity = _enterprise_identity(item)
        template_id = _template_candidate_id(item)
        evidence_id = f"exp-evid:{hashlib.sha256(item.record_identity.encode('utf-8')).hexdigest()[:24]}"
        row = asdict(item)
        row.update(
            {
                "evidence_id": evidence_id,
                "enterprise_identity": enterprise_identity,
                "template_candidate_cluster_id": template_id,
                "template_cluster_semantics": "prediction",
            }
        )
        sample_rows.append(row)
        crosswalk.append(
            {
                "evidence_id": evidence_id,
                "record_identity": item.record_identity,
                "document_id": item.document_id,
                "source_jd_id": item.source_jd_id,
                "source_fact_id": item.source_fact_id,
                "source_version": item.source_version,
                "release_ids": list(item.release_ids),
            }
        )
        templates.append(
            {
                "evidence_id": evidence_id,
                "template_candidate_cluster_id": template_id,
                "prediction_version": TEMPLATE_CANDIDATE_VERSION,
                "gold_label": None,
            }
        )
        annotations.append(
            {
                "evidence_id": evidence_id,
                "record_identity": item.record_identity,
                "position_code": item.position_code,
                "title": item.title,
                "text_excerpt": item.text_excerpt,
                "candidate_reason": "deterministic exact structural signature",
                "template_candidate_cluster_id": template_id,
                "prediction": {"same_hiring_event_cluster": template_id},
                "human_gold": {
                    "same_hiring_event_cluster": None,
                    "annotator_id": None,
                    "annotated_at": None,
                    "notes": None,
                },
            }
        )
    stable_payload = {
        "algorithm_version": FREEZE_ALGORITHM_VERSION,
        "template_candidate_version": TEMPLATE_CANDIDATE_VERSION,
        "config": dict(config),
        "records": [
            {
                "record_identity": row["record_identity"],
                "text_fingerprint": row["text_fingerprint"],
                "position_code": row["position_code"],
            }
            for row in sample_rows
        ],
    }
    dataset_version = "sha256:" + hashlib.sha256(
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "exp-evid-dataset-manifest.v1",
        "experiment_id": "EXP-EVID-01",
        "research_status": "incomplete",
        "research_status_reason": "human_gold_not_frozen",
        "dataset_version": dataset_version,
        "algorithm_version": FREEZE_ALGORITHM_VERSION,
        "template_candidate_version": TEMPLATE_CANDIDATE_VERSION,
        "target_position_codes": list(TARGET_POSITION_CODES),
        "selection_config": dict(config),
        "sample_count": len(sample_rows),
        "sample_count_by_position": dict(
            sorted(Counter(str(row["position_code"]) for row in sample_rows).items())
        ),
        "time_basis_count": dict(
            sorted(Counter(str(row["time_basis"]) for row in sample_rows).items())
        ),
        "gold_metrics": {"pairwise_f1": None, "b_cubed_f1": None},
    }
    return manifest, sample_rows, crosswalk, templates, annotations, exclusions


def write_inventory_artifacts(
    out_dir: Path,
    inventory: InventoryPayload,
    overlaps: Sequence[OverlapRow],
    exclusions: Sequence[ExclusionRow],
) -> None:
    inventory_dir = out_dir / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    _write_json(inventory_dir / "data-inventory.json", inventory)
    _write_jsonl(inventory_dir / "identity-overlap.jsonl", overlaps)
    _write_jsonl(inventory_dir / "data-exclusions.jsonl", exclusions)


def write_freeze_artifacts(
    out_dir: Path,
    manifest: FreezeManifest,
    samples: Sequence[SampleRow],
    crosswalk: Sequence[CrosswalkRow],
    templates: Sequence[TemplateRow],
    annotations: Sequence[AnnotationRow],
    exclusions: Sequence[ExclusionRow],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "dataset-manifest.json", manifest)
    _write_jsonl(out_dir / "sample-manifest.jsonl", samples)
    _write_jsonl(out_dir / "identity-crosswalk.jsonl", crosswalk)
    _write_jsonl(out_dir / "template-candidates.jsonl", templates)
    _write_jsonl(out_dir / "annotation-pack.jsonl", annotations)
    _write_jsonl(out_dir / "exclusions.jsonl", exclusions)


def _candidate(
    *,
    asset_pool: str,
    document_id: str,
    annotation: Mapping[str, object],
    normalized: Mapping[str, object],
    metadata: Mapping[str, object],
    record_identity: str,
    identity_kind: str,
    input_ref: str,
) -> RealJDCandidate:
    classification = normalized.get("job_classification") or {}
    responsibilities = tuple(
        str(item.get("value") or "").strip()
        for item in annotation.get("responsibilities") or []
        if str(item.get("value") or "").strip()
    )
    skills = tuple(sorted({
        str(skill.get("canonical_name") or skill.get("source_name") or "").strip()
        for requirement in normalized.get("normalized_requirements") or []
        for skill in requirement.get("skills") or []
        if str(skill.get("canonical_name") or skill.get("source_name") or "").strip()
    }))
    title = str((annotation.get("job_title") or {}).get("value") or classification.get("source_title") or "").strip()
    enterprise_name = _company_name(annotation.get("company_facts"))
    excerpt_parts = [title, *responsibilities[:4], *skills[:12]]
    excerpt = "\n".join(part for part in excerpt_parts if part)[:4000]
    published_at = _date_iso(metadata.get("publish_date"))
    crawl_time = _iso(metadata.get("crawl_time"))
    observed_at = _iso(metadata.get("observed_at"))
    # TEMP-LAG-01: crawl time is crawler provenance; observed_at stays pipeline
    # bookkeeping.  They are never folded into each other.
    collection_time_basis = (
        "crawler_acquired"
        if crawl_time
        else ("pipeline_observed" if observed_at else "unknown")
    )
    time_basis = (
        "published"
        if published_at
        else ("crawler" if crawl_time else ("observed" if observed_at else "unknown"))
    )
    return RealJDCandidate(
        asset_pool=asset_pool,
        record_identity=record_identity,
        document_id=document_id,
        position_code=_clean(classification.get("position_code")),
        classification_status=_clean(classification.get("classification_status")),
        source_platform=_clean(metadata.get("source_platform")),
        source_record_id=_clean(metadata.get("source_record_id")),
        source_version=_clean(metadata.get("source_version")),
        content_hash=_clean(metadata.get("content_hash")),
        source_fact_id=_clean(metadata.get("source_fact_id")),
        source_jd_id=_clean(metadata.get("source_jd_id")),
        enterprise_name=enterprise_name,
        enterprise_id=_clean(metadata.get("enterprise_id")),
        published_at=published_at,
        observed_at=observed_at,
        crawl_time=crawl_time,
        collection_time_basis=collection_time_basis,
        time_basis=time_basis,
        title=title,
        responsibilities=responsibilities,
        skills=skills,
        text_excerpt=excerpt,
        text_fingerprint=text_fingerprint(excerpt) if excerpt else "",
        release_ids=tuple(sorted(str(value) for value in metadata.get("release_ids") or [])),
        identity_kind=identity_kind,
        input_ref=input_ref,
    )


def _selected_bundle_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["document_id"]: row
            for row in csv.DictReader(handle)
            if row.get("row_outcome")
            in {"selected_success", "recovered_unique_failure"}
        }


def _mapping_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["document_id"]: row
            for row in csv.DictReader(handle)
            if row.get("document_id")
        }


def _run_rows(directory: Path) -> dict[str, int]:
    rows: dict[str, int] = {}
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        document_id = str((raw.get("annotation") or {}).get("document_id") or "")
        if document_id:
            rows[document_id] = int(raw["row_index"])
    return rows


def _jsonl_by_document(path: Path) -> dict[str, dict[str, object]]:
    return {
        str(row["document_id"]): row
        for row in _read_jsonl(path)
        if row.get("document_id")
    }


def _read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _source_identity(metadata: Mapping[str, object]) -> str:
    values = tuple(_clean(metadata.get(key)) for key in ("source_platform", "source_record_id", "content_hash"))
    if not all(values):
        raise ValueError("selected bundle row is missing explicit source identity")
    return "source:" + ":".join(str(value) for value in values)


def _enterprise_identity(item: RealJDCandidate) -> str | None:
    if item.enterprise_id:
        return f"enterprise-id:{item.enterprise_id}"
    if item.enterprise_name:
        normalized = _SPACE.sub("", item.enterprise_name).casefold()
        return "enterprise-name:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return None


def _company_name(raw: object) -> str | None:
    if isinstance(raw, Mapping):
        value = raw.get("value") if raw.get("kind") in (None, "company_name") else None
        return _clean(value)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and item.get("kind") == "company_name":
                value = _clean(item.get("value"))
                if value:
                    return value
    return None


def _template_candidate_id(item: RealJDCandidate) -> str:
    structural = "|".join((item.title, *item.responsibilities[:4]))
    normalized = _DIGIT.sub("#", _SPACE.sub(" ", structural).strip().casefold())
    return "template-candidate:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _exclusion_reason(item: RealJDCandidate) -> str | None:
    if item.position_code is None or item.classification_status is None:
        return "position_classification_missing"
    if item.position_code not in TARGET_POSITION_CODES:
        return "position_not_in_target_set"
    if item.classification_status != "resolved":
        return "position_classification_not_resolved"
    if not item.title or not item.text_excerpt:
        return "text_missing"
    if not item.published_at and not item.crawl_time and not item.observed_at:
        return "observation_time_missing"
    return None


def _exclusion_row(item: RealJDCandidate, reason: str) -> dict[str, object]:
    return {
        "record_identity": item.record_identity,
        "asset_pool": item.asset_pool,
        "document_id": item.document_id,
        "position_code": item.position_code,
        "reason": reason,
    }


def _coverage(items: Sequence[RealJDCandidate], field: str) -> float:
    return round(sum(bool(getattr(item, field)) for item in items) / len(items), 4) if items else 0.0


def _coverage_any(items: Sequence[RealJDCandidate], fields: Iterable[str]) -> float:
    fields = tuple(fields)
    return round(sum(any(bool(getattr(item, field)) for field in fields) for item in items) / len(items), 4) if items else 0.0


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _iso(value: object) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()


def _date_iso(value: object) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


__all__ = [
    "FREEZE_ALGORITHM_VERSION",
    "RealJDCandidate",
    "TARGET_POSITION_CODES",
    "TEMPLATE_CANDIDATE_VERSION",
    "build_inventory",
    "freeze_target_samples",
    "load_bundle_candidates",
    "load_run_candidates",
    "write_freeze_artifacts",
    "write_inventory_artifacts",
]
