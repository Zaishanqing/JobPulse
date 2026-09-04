"""Build EVID-PAIR-HARD-v3: a stratified pair benchmark from the frozen 365 JD set.

Candidate mining only nominates pairs worth reviewing (never Gold).  Stratified
categories A-H cover exact duplicates, cross-source reposts, same-enterprise
same/different hiring events, template/source/title hard negatives and
semantically close different roles.  Pairs are split 60/20/20 by dependency
cluster/enterprise group (GroupKFold-style), so no event leaks across splits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from app.contexts.evidence_independence.application import (
    build_independent_clusters,
)
from app.contexts.evidence_independence.contracts import EvidenceRecord


POSITIONS = ("BACKEND_ENGINEER", "LLM_ALGORITHM_ENGINEER")
RELEASE_ID = "exp-evid-real-jd-20260812-fresh-v1"
MIN_POSITIVE_PER_POSITION = 30
MIN_NEGATIVE_PER_POSITION = 80
SEED = 20260815


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formal-manifest",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "release-runs"
            / "fresh-v1"
            / "formal-sample-manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "pair-benchmark-v3"
        ),
    )
    parser.add_argument(
        "--positive-cap-per-position",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--negative-cap-per-position",
        type=int,
        default=160,
    )
    args = parser.parse_args(argv)

    rows = _read_jsonl(Path(args.formal_manifest))
    records_by_position = _records(rows)
    clusters_by_position = {
        position: build_independent_clusters(tuple(records))
        for position, records in records_by_position.items()
    }
    items: dict[str, dict] = {}
    summary: dict[str, dict] = {}
    for position in POSITIONS:
        records = records_by_position[position]
        clusters = clusters_by_position[position]
        positive = _mine_positive(
            records,
            clusters,
            position,
            limit=args.positive_cap_per_position,
        )
        negative = _mine_negative(
            records,
            clusters,
            position,
            limit=args.negative_cap_per_position,
        )
        pair_items = {
            pair["pair_id"]: {
                **_pair_view(position, pair["left"], pair["right"]),
                "source_kind": pair["kind"],
                "candidate_label": pair["candidate_label"],
            }
            for pair in (*positive, *negative)
        }
        items.update(pair_items)
        summary[position] = {
            "record_count": len(records),
            "cluster_count": len(clusters),
            "positive_candidates": len(positive),
            "negative_candidates": len(negative),
            "by_kind": _kind_counts((*positive, *negative)),
        }
    pack = {
        "schema_version": "evid-pair-hard-v3.v1",
        "experiment_id": "EXP-EVID-01",
        "release_id": RELEASE_ID,
        "seed": SEED,
        "item_count": len(items),
        "items": items,
        "summary": summary,
        "split": _group_split(items),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark-pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(out / "benchmark-pack.json")
    print(
        json.dumps(
            {
                "total": len(items),
                "summary": summary,
                "split_counts": {
                    split: sum(1 for value in pack["split"].values() if value == split)
                    for split in ("train", "val", "holdout")
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _mine_positive(
    records: list[EvidenceRecord],
    clusters: tuple[tuple[str, ...], ...],
    position: str,
    *,
    limit: int,
) -> list[dict]:
    record_by_id = {record.evidence_id: record for record in records}
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(left: EvidenceRecord, right: EvidenceRecord, kind: str) -> None:
        key = tuple(sorted((left.evidence_id, right.evidence_id)))
        if key in seen:
            return
        seen.add(key)
        pairs.append(
            {
                "pair_id": f"same:{left.evidence_id}:{right.evidence_id}",
                "left": left,
                "right": right,
                "kind": kind,
                "candidate_label": True,
            }
        )

    for cluster in clusters:
        if len(cluster) < 2:
            continue
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if left.text_fingerprint and left.text_fingerprint == right.text_fingerprint:
                    add(left, right, "A_exact_duplicate")
                elif (
                    left.normalized_url
                    and right.normalized_url
                    and _norm_url(left.normalized_url) == _norm_url(right.normalized_url)
                ):
                    add(left, right, "A_exact_duplicate")
                elif left.source_id != right.source_id and _title_overlap(
                    left.text or "", right.text or ""
                ):
                    add(left, right, "B_cross_source_repost")
                elif (
                    _same(left.enterprise_id, right.enterprise_id)
                    and _same(left.position_id, right.position_id)
                    and left.published_at
                    and right.published_at
                    and abs((left.published_at - right.published_at).days) <= 7
                ):
                    add(left, right, "C_same_enterprise_same_event")
                elif _same(left.enterprise_id, right.enterprise_id) and _text_similarity(
                    left.text or "", right.text or ""
                ) >= 0.55:
                    add(left, right, "C_same_enterprise_same_event")

    # Independent hard-positive channels decoupled from the old clustering
    # candidate distribution: major rewrite, cross-source repost with missing
    # enterprise, time-missing duplicate and template-neighbour repost.  These
    # run before the broad same-enterprise mining so each channel is labelled
    # with its actual evidence signal.
    same_position = _records_by_position(records)
    for position_records in same_position.values():
        for index, left in enumerate(position_records):
            for right in position_records[index + 1 :]:
                similarity = _text_similarity(
                    left.text or "", right.text or ""
                )
                if (
                    _same(left.enterprise_id, right.enterprise_id)
                    and 0.35 <= similarity < 0.55
                    and _same(left.position_id, right.position_id)
                    and not _same(
                        left.template_cluster_id,
                        right.template_cluster_id,
                    )
                ):
                    add(left, right, "D_major_rewrite_same_event")
                elif (
                    left.source_id != right.source_id
                    and (not left.enterprise_id or not right.enterprise_id)
                    and _same(left.position_id, right.position_id)
                    and (
                        _title_overlap(left.text or "", right.text or "")
                        or similarity >= 0.35
                    )
                ):
                    add(
                        left,
                        right,
                        "F_enterprise_missing_cross_source_repost",
                    )
                elif (
                    similarity >= 0.6
                    and _same(left.position_id, right.position_id)
                    and (not left.published_at or not right.published_at)
                    and (
                        _same(left.source_id, right.source_id)
                        or _same(left.enterprise_id, right.enterprise_id)
                    )
                ):
                    add(left, right, "G_time_missing_duplicate")
                elif (
                    _same(left.template_cluster_id, right.template_cluster_id)
                    and _same(left.enterprise_id, right.enterprise_id)
                    and _same(left.position_id, right.position_id)
                    and similarity >= 0.4
                ):
                    add(left, right, "H_template_neighbour_repost")

    # Cross-cluster same-enterprise mining: different clusters for the same
    # enterprise with strong text overlap are still positive candidates.
    by_enterprise: dict[str, list[EvidenceRecord]] = {}
    for record in records:
        if record.enterprise_id:
            by_enterprise.setdefault(record.enterprise_id, []).append(record)
    for enterprise_records in by_enterprise.values():
        for index, left in enumerate(enterprise_records):
            for right in enterprise_records[index + 1 :]:
                if left.evidence_id == right.evidence_id:
                    continue
                if _text_similarity(left.text or "", right.text or "") >= 0.55:
                    add(left, right, "C_same_enterprise_same_event")
                elif (
                    left.published_at
                    and right.published_at
                    and abs((left.published_at - right.published_at).days) <= 7
                ):
                    add(left, right, "C_same_enterprise_same_event")

    pairs.sort(key=lambda item: (item["kind"], item["pair_id"]))
    return pairs[:limit]


def _records_by_position(
    records: list[EvidenceRecord],
) -> dict[str, list[EvidenceRecord]]:
    grouped: dict[str, list[EvidenceRecord]] = {}
    for record in records:
        grouped.setdefault(str(record.position_id or "unknown"), []).append(
            record
        )
    return grouped


def _mine_negative(
    records: list[EvidenceRecord],
    clusters: tuple[tuple[str, ...], ...],
    position: str,
    *,
    limit: int,
) -> list[dict]:
    record_by_id = {record.evidence_id: record for record in records}
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    reps = [record_by_id[cluster[0]] for cluster in clusters]

    def add(left: EvidenceRecord, right: EvidenceRecord, kind: str) -> None:
        key = tuple(sorted((left.evidence_id, right.evidence_id)))
        if key in seen:
            return
        seen.add(key)
        pairs.append(
            {
                "pair_id": f"cross:{left.evidence_id}:{right.evidence_id}",
                "left": left,
                "right": right,
                "kind": kind,
                "candidate_label": False,
            }
        )

    for index, left in enumerate(reps):
        for right in reps[index + 1 :]:
            if _same(left.template_cluster_id, right.template_cluster_id) and not _same(
                left.enterprise_id, right.enterprise_id
            ):
                add(left, right, "E_same_template_diff_enterprise")
            elif _same(left.enterprise_id, right.enterprise_id) and not _same(
                left.template_cluster_id, right.template_cluster_id
            ):
                add(left, right, "D_same_enterprise_diff_event")
            elif _same(left.source_id, right.source_id) and not _same(
                left.enterprise_id, right.enterprise_id
            ):
                add(left, right, "G_same_source_independent")
            elif _title_overlap(left.text or "", right.text or "") and _text_similarity(
                left.text or "", right.text or ""
            ) < 0.45:
                add(left, right, "F_similar_title_diff_duty")
    pairs.sort(key=lambda item: (item["kind"], item["pair_id"]))
    return pairs[:limit]


def _group_split(items: dict[str, dict]) -> dict[str, str]:
    """GroupKFold-style split keyed by the left/right enterprise or cluster."""

    split: dict[str, str] = {}
    group_by_pair: dict[str, str] = {}
    for pair_id, item in items.items():
        left = item.get("left") or {}
        right = item.get("right") or {}
        group = left.get("enterprise") or right.get("enterprise") or left.get("template_cluster") or "unknown"
        group_by_pair[pair_id] = group
    for pair_id, group in group_by_pair.items():
        digest = hashlib.sha256(group.encode("utf-8")).hexdigest()
        value = int(digest, 16) % 10
        split[pair_id] = "train" if value < 6 else "val" if value < 8 else "holdout"
    return split


def _kind_counts(pairs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pair in pairs:
        counts[pair["kind"]] = counts.get(pair["kind"], 0) + 1
    return counts


def _pair_view(position: str, left: EvidenceRecord, right: EvidenceRecord) -> dict:
    return {
        "position": position,
        "left": _evidence_view(left),
        "right": _evidence_view(right),
    }


def _evidence_view(record: EvidenceRecord) -> dict:
    return {
        "evidence_id": record.evidence_id,
        "source": record.source_id,
        "enterprise": record.enterprise_id,
        "template_cluster": record.template_cluster_id,
        "text_fingerprint": record.text_fingerprint,
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "observed_at": record.collected_at.isoformat() if record.collected_at else None,
        "text_excerpt": _truncate(record.text or "", 420),
    }


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


def _same(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _norm_url(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _title_overlap(left_text: str, right_text: str) -> bool:
    left_title = left_text.splitlines()[0] if left_text else ""
    right_title = right_text.splitlines()[0] if right_text else ""
    left_tokens = set(_tokens(left_title))
    right_tokens = set(_tokens(right_title))
    return len(left_tokens & right_tokens) >= 2


def _text_similarity(left_text: str, right_text: str) -> float:
    left_tokens = set(_tokens(left_text))
    right_tokens = set(_tokens(right_text))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _tokens(value: str) -> list[str]:
    import re

    return [
        token
        for token in re.split(r"[^\w\u4e00-\u9fff]+", value.casefold())
        if token
    ]


def _records(rows: list[dict]) -> dict[str, list[EvidenceRecord]]:
    records_by_position: dict[str, list[EvidenceRecord]] = {}
    for row in rows:
        position = str(row["position_code"])
        records_by_position.setdefault(position, []).append(_record(row))
    return records_by_position


def _record(row: dict) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=str(row["evidence_id"]),
        subject_ref=str(row["position_code"]),
        source_id=str(row["source_platform"]),
        enterprise_id=_optional(
            row.get("enterprise_identity") or row.get("enterprise_id")
        ),
        text_fingerprint=_optional(row.get("text_fingerprint")),
        position_id=str(row["position_code"]),
        published_at=(
            date.fromisoformat(str(row["published_at"]))
            if row.get("published_at")
            else None
        ),
        collected_at=(
            datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if row.get("observed_at")
            else None
        ),
        template_cluster_id=_optional(row.get("template_candidate_cluster_id")),
        release_id=RELEASE_ID,
        source_version=_optional(row.get("source_fact_version")),
        text=_optional(row.get("text_excerpt")),
    )


def _optional(value: object) -> str | None:
    return str(value) if value is not None and str(value).strip() else None


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


if __name__ == "__main__":
    raise SystemExit(main())
