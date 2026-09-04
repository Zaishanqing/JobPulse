from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contexts.catalog._applications.normalization_suggestions import (  # noqa: E402
    rank_normalization_suggestions,
)
from app.contexts.catalog._ports.skills import SkillAliasRecord, SkillRecord  # noqa: E402
from app.domain.skills import find_text_match  # noqa: E402
from cached_catalog_embedding import (  # noqa: E402
    CachedCatalogEmbedding,
    CatalogEmbeddingClient,
)


CATALOG_PATH = ROOT / "config" / "skill_taxonomy_catalog.v1.json"
NORMALIZATION_MAP_PATH = (
    ROOT.parent
    / "Extraction"
    / "cvextraction"
    / "resources"
    / "normalization"
    / "2.0"
    / "normalization_map.yaml"
)
CASES_PATH = ROOT / "docs" / "evaluation" / "d4_normalization_cases.json"
REPORT_PATH = ROOT / "reports" / "d4_normalization_topk_evaluation.json"


def load_catalog() -> tuple[list[SkillRecord], list[SkillAliasRecord]]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    skills = [
        SkillRecord(
            skill_id=skill_id,
            skill_name=item["canonical_name"],
            catalog_code=skill_id,
            category=None,
            description=None,
            parent_skill_id=None,
            status="active",
            redirect_target_skill_id=None,
            created_at=None,
            updated_at=None,
        )
        for skill_id, item in payload["skills"].items()
    ]
    skill_ids = {item.skill_id for item in skills}
    normalization = yaml.safe_load(
        NORMALIZATION_MAP_PATH.read_text(encoding="utf-8")
    )["skills"]
    aliases = []
    for index, (expression, item) in enumerate(normalization.items(), start=1):
        if (
            item["skill_id"] in skill_ids
            and expression.casefold() != item["canonical_name"].casefold()
        ):
            aliases.append(
                SkillAliasRecord(
                    alias_id=f"eval-alias-{index}",
                    skill_id=item["skill_id"],
                    alias=expression,
                )
            )
    return skills, aliases


def metrics(ranks: list[int | None]) -> dict[str, float]:
    count = len(ranks)
    return {
        "recall_at_1": round(sum(rank == 1 for rank in ranks) / count, 6),
        "recall_at_3": round(
            sum(rank is not None and rank <= 3 for rank in ranks) / count, 6
        ),
        "recall_at_5": round(
            sum(rank is not None and rank <= 5 for rank in ranks) / count, 6
        ),
        "mrr": round(sum(1 / rank for rank in ranks if rank is not None) / count, 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate D4 normalization Top-K")
    parser.add_argument("--embedding-url")
    parser.add_argument("--embedding-model", default="BAAI/bge-m3")
    parser.add_argument(
        "--embedding-revision",
        default="5617a9f61b028005a4858fdac845db406aefb181",
    )
    parser.add_argument("--embedding-dimension", type=int, default=1024)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    embedding = None
    if args.embedding_url:
        embedding = CachedCatalogEmbedding(
            CatalogEmbeddingClient(
                args.embedding_url,
                model=args.embedding_model,
                revision=args.embedding_revision,
                dimension=args.embedding_dimension,
            )
        )
    skills, aliases = load_catalog()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    skill_pairs = tuple((item.skill_id, item.skill_name) for item in skills)
    alias_pairs = tuple((item.skill_id, item.alias) for item in aliases)
    ranks: dict[str, list[int | None]] = defaultdict(list)
    failures: list[dict[str, object]] = []
    no_mapping = {"count": 0, "baseline_suggested": 0, "lexical_ge_0_9": 0, "hybrid_ge_0_9": 0}
    semantic_available = False

    for case in cases:
        baseline = find_text_match(case["raw_skill"], skill_pairs, alias_pairs)
        lexical = rank_normalization_suggestions(
            raw_skill=case["raw_skill"], context=case["context"], skills=skills,
            aliases=aliases, reviewed_skill_id=None, top_k=5, embedding=None,
        )
        hybrid = rank_normalization_suggestions(
            raw_skill=case["raw_skill"], context=case["context"], skills=skills,
            aliases=aliases, reviewed_skill_id=None, top_k=5, embedding=embedding,
        )
        semantic_available = semantic_available or bool(
            hybrid and hybrid[0].semantic_available
        )
        expected = case["expected_skill_id"]
        if expected is None:
            no_mapping["count"] += 1
            no_mapping["baseline_suggested"] += int(baseline is not None)
            no_mapping["lexical_ge_0_9"] += int(
                bool(lexical and lexical[0].combined_score >= 0.9)
            )
            no_mapping["hybrid_ge_0_9"] += int(
                bool(hybrid and hybrid[0].combined_score >= 0.9)
            )
            continue
        baseline_rank = 1 if baseline and baseline.skill_id == expected else None
        lexical_rank = next(
            (item.rank for item in lexical if item.skill_id == expected), None
        )
        hybrid_rank = next(
            (item.rank for item in hybrid if item.skill_id == expected), None
        )
        ranks["find_text_match"].append(baseline_rank)
        ranks["lexical_top_k"].append(lexical_rank)
        ranks["hybrid_top_k"].append(hybrid_rank)
        if any(rank != 1 for rank in (baseline_rank, lexical_rank, hybrid_rank)):
            failures.append(
                {
                    "raw_skill": case["raw_skill"],
                    "group": case["group"],
                    "expected_skill_id": expected,
                    "baseline_rank": baseline_rank,
                    "lexical_rank": lexical_rank,
                    "hybrid_rank": hybrid_rank,
                    "lexical_top_5": [item.skill_id for item in lexical],
                    "hybrid_top_5": [item.skill_id for item in hybrid],
                }
            )

    report = {
        "evaluation": "d4-normalization-hybrid-top-k.v1",
        "catalog_path": str(CATALOG_PATH.relative_to(ROOT)),
        "catalog_skill_count": len(skills),
        "alias_source": str(NORMALIZATION_MAP_PATH.relative_to(ROOT.parent)),
        "alias_count": len(aliases),
        "case_count": len(cases),
        "positive_case_count": len(ranks["lexical_top_k"]),
        "no_mapping_case_count": no_mapping["count"],
        "hybrid_weights": {"lexical": 0.55, "semantic": 0.45},
        "semantic_available": semantic_available,
        "metrics": {name: metrics(values) for name, values in ranks.items()},
        "no_mapping": no_mapping,
        "non_top1_or_missed_cases": failures,
        "notes": [
            "Recall and MRR use only cases with a real Catalog target.",
            "No-mapping cases measure whether a method emits a high-confidence suggestion; suggestions never auto-map.",
            (
                "Hybrid used embedding-service.v1 cosine scores."
                if semantic_available
                else "Embedding service was not configured or unavailable; Hybrid degraded to Lexical and therefore has identical metrics."
            ),
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
