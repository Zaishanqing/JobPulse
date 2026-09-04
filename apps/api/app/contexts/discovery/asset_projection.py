"""Read projection of accepted discovery assets, independent of publication records."""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5


def emerging_asset(cluster: dict, experiment_id: str) -> dict:
    definition = deepcopy(cluster["definition"])
    fields = definition.get("field_evidence") or {}
    skills = {}
    for field_name in ("required_skills", "bonus_skills"):
        items = (fields.get(field_name) or {}).get("items") or []
        by_name = {item.get("content"): item for item in items}
        skills[field_name] = [
            {**by_name.get(row.get("raw_skill"), {}), **row}
            for row in definition.get(field_name, [])
        ]
    return {
        "emerging_id": f"formal:{cluster['cluster_key']}",
        "governance_id": str(uuid5(NAMESPACE_URL, f"formal-emerge-v3.2:{cluster['cluster_key']}")),
        "cluster_id": cluster["cluster_key"],
        "position_name": definition.get("position_name") or cluster["canonical_title"],
        "core_responsibilities": definition.get("core_responsibilities", []),
        **skills,
        "industry_scenarios": definition.get("industry_scenarios", []),
        "field_evidence": fields,
        "asset_definition": definition,
        "source_kind": "discovery_asset",
        "experiment_id": experiment_id,
        "support_jd_count": cluster["counts"]["independent_postings"],
        "source_count": cluster["counts"]["sources"],
        "enterprise_count": cluster["counts"]["enterprises"],
        "status": "discovered",
        "evidence_jd_ids": [],
        "germination_score": None,
        "score_dimensions": {},
    }
