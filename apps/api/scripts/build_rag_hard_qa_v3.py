"""Build RAG-QA-HARD-01 v4: same-document constrained hard QA manifest.

Round 3 fixes the evaluator ceiling in the v1 manifest: multi_evidence and
partial_evidence cases must draw their expected Evidence from one document,
otherwise the metadata hard filter (``business_object.object_id`` =
``source_document_id``) can never recall more than one of the expected
references.  Suggestion values stay deterministic scenario construction and
are AI-reviewed proxy labels, never human Gold.  Round 4 adds open-vocabulary,
abbreviation and implicit-skill cases so the benchmark exercises synonyms and
non-literal intents instead of only the fixed ``TECH_TERMS`` vocabulary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rag_snapshot_versions import graph_version_by_document


TARGET_QA = 120
SCENARIO_TARGETS = {
    "answerable_direct": 20,
    "multi_evidence": 15,
    "partial_evidence": 0,
    "sufficient_evidence": 10,
    "insufficient_evidence": 5,
    "unanswerable_near_match": 15,
    "conflicting_evidence": 5,
    "conflict_pending": 5,
    "wrong_version": 10,
    "wrong_tenant": 5,
    "stale_evidence": 5,
    "ambiguous_question": 5,
    "open_vocabulary": 10,
    "abbreviation_query": 5,
    "implicit_skill": 5,
}
TECH_TERMS = (
    "Python",
    "Java",
    "MySQL",
    "Redis",
    "Docker",
    "Kubernetes",
    "Spring Boot",
    "机器学习",
    "大模型",
    "分布式系统",
    "微服务",
    "PyTorch",
)
OPEN_VOCAB_ALIASES = {
    "LLMOps": "大模型运维",
    "Kubernetes": "K8s容器编排",
    "PyTorch": "深度学习框架",
    "MySQL": "关系型数据库",
    "Redis": "缓存中间件",
    "微服务": "微服务治理",
    "分布式系统": "分布式系统开发",
}
IMPLICIT_QUERIES = {
    "Docker": "是否要求具备容器化部署能力",
    "Kubernetes": "是否要求能管理大规模容器集群",
    "机器学习": "是否要求能做模型训练与效果调优",
    "大模型": "是否要求具备大模型应用落地经验",
    "Redis": "是否要求熟悉高速缓存场景",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=str(
            Path(__file__).resolve().parents[1]
            / "demo-snapshot"
            / "knowledge-graph"
            / "knowledge-graph.db"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "innovation"
            / "RAG-QA-HARD-01"
            / "v3"
        ),
    )
    parser.add_argument("--seed", type=int, default=20260815)
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"snapshot db not found: {db_path}", file=sys.stderr)
        return 2
    evidence = _load_evidence(db_path)
    if len(evidence) < TARGET_QA:
        print(f"not enough real evidence: {len(evidence)}", file=sys.stderr)
        return 2
    cases = _freeze_hard_qa(evidence, args.seed)
    if len(cases) != TARGET_QA:
        print(f"hard QA count mismatch: {len(cases)}", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "rag-hard-qa-manifest.v4",
        "experiment_id": "RAG-QA-HARD-01",
        "dataset_version": _dataset_version(cases),
        "provenance": (
            "real Evidence from jobgraph-demo-snapshot knowledge-graph.db "
            "extraction_evidence; same-document constrained scenarios; "
            "constructed deterministically; open-vocabulary/abbreviation/"
            "implicit-skill cases use query-side synonyms"
        ),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "gold_status": "ai_reviewed_proxy",
        "gold_note": (
            "suggestion answerability is deterministic scenario construction "
            "reviewed as AI proxy; not human gold"
        ),
        "scenario_targets": SCENARIO_TARGETS,
        "sampling": {"seed": args.seed, "target": TARGET_QA},
        "cases": cases,
    }
    (out_dir / "qa-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(out_dir / "qa-manifest.json")
    print(json.dumps({"cases": len(cases)}, ensure_ascii=False))
    return 0


def _load_evidence(db_path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, document_id, owner_type, owner_ref, quote, start, end,
                   alignment, occurrence_index, created_at
            FROM extraction_evidence
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        version_by_document = graph_version_by_document(cur)
    finally:
        con.close()
    result: list[dict] = []
    for row in rows:
        (
            evidence_id,
            document_id,
            owner_type,
            owner_ref,
            quote,
            start,
            end,
            alignment,
            occurrence_index,
            created_at,
        ) = row
        result.append(
            {
                "evidence_id": f"snapshot-evidence:{evidence_id}",
                "document_id": str(document_id),
                "owner_type": str(owner_type),
                "owner_ref": str(owner_ref),
                "quote": str(quote),
                "start": int(start) if start is not None else None,
                "end": int(end) if end is not None else None,
                "alignment": str(alignment),
                "occurrence_index": int(occurrence_index)
                if occurrence_index is not None
                else 0,
                "source_version": str(created_at),
                "graph_version_id": version_by_document.get(str(document_id)),
            }
        )
    return result


def _freeze_hard_qa(evidence: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    term_evidence = [
        item
        for item in evidence
        if item["quote"] and any(term in item["quote"] for term in TECH_TERMS)
    ]
    term_evidence = sorted(term_evidence, key=lambda item: item["evidence_id"])
    rng.shuffle(term_evidence)
    non_term = [
        item
        for item in evidence
        if item not in term_evidence and item["quote"]
    ]
    rng.shuffle(non_term)
    by_document: dict[str, list[dict]] = defaultdict(list)
    for item in term_evidence:
        by_document[item["document_id"]].append(item)
    non_term_by_document: dict[str, list[dict]] = defaultdict(list)
    for item in non_term:
        non_term_by_document[item["document_id"]].append(item)
    cases: list[dict] = []

    for index in range(SCENARIO_TARGETS["answerable_direct"]):
        item = term_evidence[index % len(term_evidence)]
        term = next(term for term in TECH_TERMS if term in item["quote"])
        cases.append(
            _case(
                qa_id=f"hard-answerable-{index + 1:02d}",
                query_text=f"该 JD 是否明确要求 {term}？",
                evidence_items=[item],
                scenario="answerable_direct",
                suggested_answerable=True,
            )
        )

    documents_with_two = [
        document_id
        for document_id, items in by_document.items()
        if len(items) >= 2
    ]
    rng.shuffle(documents_with_two)
    for index in range(SCENARIO_TARGETS["multi_evidence"]):
        document_id = documents_with_two[
            index % max(len(documents_with_two), 1)
        ]
        group = by_document[document_id][:2]
        terms = [
            next(term for term in TECH_TERMS if term in item["quote"])
            for item in group
            if any(term in item["quote"] for term in TECH_TERMS)
        ]
        if not terms:
            terms = ["Python", "Java"]
        cases.append(
            _case(
                qa_id=f"hard-multi-{index + 1:02d}",
                query_text=f"该 JD 是否同时要求 {terms[0]} 与 {terms[-1]}？",
                evidence_items=group,
                scenario="multi_evidence",
                suggested_answerable=True,
            )
        )

    sufficient_candidates: list[tuple[list[dict], str]] = []
    for document_id, items in by_document.items():
        by_term: dict[str, list[dict]] = {}
        for item in items:
            for term in TECH_TERMS:
                if term in item["quote"]:
                    by_term.setdefault(term, []).append(item)
        for term, group in by_term.items():
            if len(group) >= 2:
                sufficient_candidates.append((group[:2], term))
    rng.shuffle(sufficient_candidates)
    for index in range(SCENARIO_TARGETS["sufficient_evidence"]):
        if sufficient_candidates:
            group, term = sufficient_candidates[
                index % len(sufficient_candidates)
            ]
        else:
            document_id = documents_with_two[
                index % max(len(documents_with_two), 1)
            ]
            group = by_document[document_id][:2]
            term = next(
                (
                    candidate_term
                    for candidate_term in TECH_TERMS
                    if candidate_term in group[0]["quote"]
                ),
                TECH_TERMS[index % len(TECH_TERMS)],
            )
        cases.append(
            _case(
                qa_id=f"hard-sufficient-{index + 1:02d}",
                query_text=f"该 JD 是否要求 {term} 并有足够证据？",
                evidence_items=group,
                scenario="sufficient_evidence",
                suggested_answerable=True,
            )
        )

    for index in range(SCENARIO_TARGETS["insufficient_evidence"]):
        item = term_evidence[(index + 20) % max(len(term_evidence), 1)]
        term = next(
            (term for term in TECH_TERMS if term in item["quote"]),
            TECH_TERMS[index % len(TECH_TERMS)],
        )
        distractors = non_term_by_document.get(item["document_id"], [])
        distractor = (
            distractors[index % max(len(distractors), 1)]
            if distractors
            else None
        )
        cases.append(
            _case(
                qa_id=f"hard-insufficient-{index + 1:02d}",
                query_text=f"该 JD 是否要求 {term} 并有足够证据？",
                evidence_items=[item],
                scenario="insufficient_evidence",
                suggested_answerable=False,
                distractor_evidence_ids=(
                    [distractor["evidence_id"]] if distractor else []
                ),
            )
        )

    for index in range(SCENARIO_TARGETS["unanswerable_near_match"]):
        item = non_term[index % max(len(non_term), 1)]
        term = TECH_TERMS[index % len(TECH_TERMS)]
        cases.append(
            _case(
                qa_id=f"hard-nearmatch-{index + 1:02d}",
                query_text=f"该 JD 是否要求 {term}？",
                evidence_items=[item],
                scenario="unanswerable_near_match",
                suggested_answerable=False,
            )
        )

    same_doc_pairs: list[tuple[dict, dict]] = []
    same_doc_same_term: list[tuple[dict, dict, str]] = []
    for document_id, items in by_document.items():
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                same_doc_pairs.append((left, right))
                left_term = next(
                    (
                        term
                        for term in TECH_TERMS
                        if term in left["quote"]
                    ),
                    None,
                )
                right_term = next(
                    (
                        term
                        for term in TECH_TERMS
                        if term in right["quote"]
                    ),
                    None,
                )
                if left_term and left_term == right_term:
                    same_doc_same_term.append((left, right, left_term))
    rng.shuffle(same_doc_same_term)
    rng.shuffle(same_doc_pairs)
    for index in range(SCENARIO_TARGETS["conflicting_evidence"]):
        if same_doc_same_term:
            left, right, term = same_doc_same_term[
                index % len(same_doc_same_term)
            ]
            cases.append(
                _case(
                    qa_id=f"hard-conflict-{index + 1:02d}",
                    query_text=f"该 JD 对 {term} 的要求是否存在冲突？",
                    evidence_items=[left, right],
                    scenario="conflicting_evidence",
                    suggested_answerable=False,
                )
            )
        else:
            first = term_evidence[(index + 40) % max(len(term_evidence), 1)]
            second = term_evidence[(index + 60) % max(len(term_evidence), 1)]
            cases.append(
                _case(
                    qa_id=f"hard-conflict-{index + 1:02d}",
                    query_text="该 JD 是否存在同概念冲突证据？",
                    evidence_items=[first, second],
                    scenario="conflict_pending",
                    suggested_answerable=False,
                    conflict_note=(
                        "no same-document same-concept conflict pair "
                        "available; protocol pending"
                    ),
                )
            )

    for index in range(SCENARIO_TARGETS["conflict_pending"]):
        if same_doc_pairs:
            left, right = same_doc_pairs[
                index % len(same_doc_pairs)
            ]
            evidence_items = [left, right]
        else:
            first = term_evidence[(index + 40) % max(len(term_evidence), 1)]
            second = term_evidence[(index + 60) % max(len(term_evidence), 1)]
            evidence_items = [first, second]
        cases.append(
            _case(
                qa_id=f"hard-conflict-pending-{index + 1:02d}",
                query_text="该 JD 是否存在同概念冲突证据？",
                evidence_items=evidence_items,
                scenario="conflict_pending",
                suggested_answerable=False,
                conflict_note="conflict protocol pending",
            )
        )

    for index in range(SCENARIO_TARGETS["wrong_version"]):
        item = term_evidence[(index + 80) % max(len(term_evidence), 1)]
        term = next(term for term in TECH_TERMS if term in item["quote"])
        cases.append(
            _case(
                qa_id=f"hard-version-{index + 1:02d}",
                query_text=f"该 JD 是否要求 {term}？",
                evidence_items=[item],
                scenario="wrong_version",
                suggested_answerable=False,
                requested_version_override=(item["graph_version_id"] or 1) + 100,
            )
        )

    for index in range(SCENARIO_TARGETS["wrong_tenant"]):
        item = term_evidence[(index + 100) % max(len(term_evidence), 1)]
        term = next(term for term in TECH_TERMS if term in item["quote"])
        cases.append(
            _case(
                qa_id=f"hard-tenant-{index + 1:02d}",
                query_text=f"该 JD 是否要求 {term}？",
                evidence_items=[item],
                scenario="wrong_tenant",
                suggested_answerable=False,
                tenant_override="enterprise:other-tenant",
            )
        )

    for index in range(SCENARIO_TARGETS["stale_evidence"]):
        item = term_evidence[(index + 120) % max(len(term_evidence), 1)]
        term = next(term for term in TECH_TERMS if term in item["quote"])
        cases.append(
            _case(
                qa_id=f"hard-stale-{index + 1:02d}",
                query_text=f"该 JD 当前版本是否要求 {term}？",
                evidence_items=[item],
                scenario="stale_evidence",
                suggested_answerable=False,
                stale_override=True,
            )
        )

    for index in range(SCENARIO_TARGETS["ambiguous_question"]):
        item = term_evidence[(index + 140) % max(len(term_evidence), 1)]
        cases.append(
            _case(
                qa_id=f"hard-ambiguous-{index + 1:02d}",
                query_text="该 JD 的必备技能是什么？",
                evidence_items=[item],
                scenario="ambiguous_question",
                suggested_answerable=False,
            )
        )

    open_term_evidence = [
        item
        for item in evidence
        if item["quote"]
        and any(
            canonical in item["quote"]
            for canonical in OPEN_VOCAB_ALIASES
        )
    ]
    open_term_evidence = sorted(
        open_term_evidence, key=lambda item: item["evidence_id"]
    )
    rng.shuffle(open_term_evidence)
    for index in range(SCENARIO_TARGETS["open_vocabulary"]):
        item = open_term_evidence[index % max(len(open_term_evidence), 1)]
        canonical = next(
            canonical
            for canonical in OPEN_VOCAB_ALIASES
            if canonical in item["quote"]
        )
        alias = OPEN_VOCAB_ALIASES[canonical]
        cases.append(
            _case(
                qa_id=f"hard-open-{index + 1:02d}",
                query_text=f"该 JD 是否要求{alias}？",
                evidence_items=[item],
                scenario="open_vocabulary",
                suggested_answerable=True,
            )
        )

    abbreviation_evidence = [
        item
        for item in evidence
        if item["quote"]
        and any(
            canonical in item["quote"]
            for canonical in ("Kubernetes", "MySQL", "Redis", "PyTorch")
        )
    ]
    abbreviation_evidence = sorted(
        abbreviation_evidence, key=lambda item: item["evidence_id"]
    )
    rng.shuffle(abbreviation_evidence)
    abbreviation_terms = (
        ("Kubernetes", "K8s"),
        ("MySQL", "SQL数据库"),
        ("Redis", "缓存中间件"),
        ("PyTorch", "PyTorch框架"),
        ("Kubernetes", "k8s"),
    )
    for index in range(SCENARIO_TARGETS["abbreviation_query"]):
        canonical, alias = abbreviation_terms[
            index % len(abbreviation_terms)
        ]
        item = next(
            (
                candidate
                for candidate in abbreviation_evidence
                if canonical in candidate["quote"]
            ),
            abbreviation_evidence[index % max(len(abbreviation_evidence), 1)],
        )
        cases.append(
            _case(
                qa_id=f"hard-abbrev-{index + 1:02d}",
                query_text=f"该 JD 是否要求{alias}？",
                evidence_items=[item],
                scenario="abbreviation_query",
                suggested_answerable=True,
            )
        )

    implicit_evidence = [
        item
        for item in evidence
        if item["quote"]
        and any(
            canonical in item["quote"]
            for canonical in IMPLICIT_QUERIES
        )
    ]
    implicit_evidence = sorted(
        implicit_evidence, key=lambda item: item["evidence_id"]
    )
    rng.shuffle(implicit_evidence)
    implicit_pairs = list(IMPLICIT_QUERIES.items())
    for index in range(SCENARIO_TARGETS["implicit_skill"]):
        canonical, query_text = implicit_pairs[
            index % len(implicit_pairs)
        ]
        item = next(
            (
                candidate
                for candidate in implicit_evidence
                if canonical in candidate["quote"]
            ),
            implicit_evidence[index % max(len(implicit_evidence), 1)],
        )
        cases.append(
            _case(
                qa_id=f"hard-implicit-{index + 1:02d}",
                query_text=f"该 JD {query_text}？",
                evidence_items=[item],
                scenario="implicit_skill",
                suggested_answerable=True,
            )
        )
    cases.sort(key=lambda item: item["qa_id"])
    return cases


def _case(
    *,
    qa_id: str,
    query_text: str,
    evidence_items: list[dict],
    scenario: str,
    suggested_answerable: bool,
    requested_version_override: int | None = None,
    tenant_override: str | None = None,
    stale_override: bool = False,
    distractor_evidence_ids: list[str] | None = None,
    conflict_note: str | None = None,
) -> dict:
    visible = []
    for item in evidence_items:
        visible.append(
            {
                "evidence_id": item["evidence_id"],
                "source_object_type": "extraction_evidence",
                "source_object_id": item["owner_ref"],
                "source_document_id": item["document_id"],
                "source_version": (
                    "2020-01-01 00:00:00"
                    if stale_override
                    else item["source_version"]
                ),
                "quote": item["quote"],
                "location_start": item["start"],
                "location_end": item["end"],
                "occurrence_index": item["occurrence_index"],
                "alignment": item["alignment"],
                "graph_version_id": item["graph_version_id"],
                "tenant_ref": "jobgraph-platform-public",
                "permission_scope": "platform:public",
            }
        )
    requested_version = (
        requested_version_override
        if requested_version_override is not None
        else evidence_items[0]["graph_version_id"]
    )
    tenant_ref = tenant_override or "jobgraph-platform-public"
    return {
        "qa_id": qa_id,
        "query_text": query_text,
        "scenario": scenario,
        "requested_identity": {
            "graph_version_id": requested_version,
            "tenant_ref": tenant_ref,
            "permission_scope": "platform:public",
            "evidence_types": ["jd_evidence"],
            "business_object": {
                "object_type": "source_jd",
                "object_id": evidence_items[0]["document_id"],
            },
        },
        "visible_evidence": visible,
        "distractor_evidence_ids": distractor_evidence_ids or [],
        "conflict_note": conflict_note,
        "gold": {
            "answerable": None,
            "allowed_references": None,
            "annotator_id": None,
            "frozen_at": None,
        },
        "suggestion": {
            "answerable": suggested_answerable,
            "source": "deterministic same-document scenario; AI-reviewed proxy, not human gold",
        },
    }


def _dataset_version(cases: list[dict]) -> str:
    payload = sorted(
        case["qa_id"]
        + "|"
        + case["query_text"]
        + "|"
        + ",".join(
            "{id}:{version}:{source}".format(
                id=ref["evidence_id"],
                version=ref.get("graph_version_id"),
                source=ref.get("source_version"),
            )
            for ref in case["visible_evidence"]
        )
        for case in cases
    )
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


if __name__ == "__main__":
    raise SystemExit(main())
