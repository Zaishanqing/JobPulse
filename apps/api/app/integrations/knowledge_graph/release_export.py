"""Produce a fully verified, immutable KG Release package from publications."""

from __future__ import annotations

from datetime import datetime
import gzip
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.integrations.knowledge_graph.mappings import (
    extraction_to_kg,
    normalization_to_kg,
)
from app.integrations.knowledge_graph.published_fact import (
    map_published_jd_fact_v3,
    publication_snapshot_views,
)
from app.models.jd_publication import JDPublication
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.standard_position import StandardPosition
from jobgraph_contracts.release_manifest import ReleaseManifestV1


def export_kg_release(
    session: Session,
    output_dir: Path,
    *,
    release_id: str,
    window_start: datetime,
    window_end: datetime,
    git_commit: str,
    mode: str = "full",
    parent_release_id: str | None = None,
) -> ReleaseManifestV1:
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("release observation window must be timezone-aware")
    if output_dir.exists():
        raise FileExistsError(f"release output already exists: {output_dir}")
    aliases_by_skill: dict[str, list[str]] = {}
    for alias in session.query(SkillAlias).order_by(SkillAlias.skill_id, SkillAlias.alias):
        aliases_by_skill.setdefault(alias.skill_id, []).append(alias.alias)
    kg_skills = [
        {
            "skill_id": skill.id,
            "canonical_name": skill.skill_name,
            "category_code": skill.category,
            "subcategory_code": None,
            "aliases": aliases_by_skill.get(skill.id, []),
            "status": "active",
        }
        for skill in session.query(Skill).order_by(Skill.id)
    ]
    kg_positions = [
        {
            "position_id": position.position_code,
            "position_code": position.position_code,
            "name": position.position_name,
            "taxonomy_version": position.taxonomy_version,
            "status": position.lifecycle_status,
            "sample_support_status": position.sample_support_status,
        }
        for position in session.query(StandardPosition)
        .filter(StandardPosition.lifecycle_status == "active")
        .order_by(StandardPosition.id)
    ]
    explicit_skill_mappings = {
        row.main_system_id: row.knowledge_graph_id
        for row in session.query(KnowledgeGraphEntityMapping)
        .filter(
            KnowledgeGraphEntityMapping.entity_type == "skill",
            KnowledgeGraphEntityMapping.sync_status == "confirmed",
            KnowledgeGraphEntityMapping.knowledge_graph_id.isnot(None),
        )
        .order_by(KnowledgeGraphEntityMapping.main_system_id)
        if row.knowledge_graph_id is not None
    }
    # Older deployed publishers emitted the v3 catalog reference without the
    # content hash. Release construction freezes the same database catalogs
    # with the current deterministic identity algorithm so the artifact meets
    # the authoritative PublishedJDFactV3 contract.
    from app.infrastructure.data_validation import (
        frozen_catalog_identity,
        frozen_position_catalog_identity,
    )

    skill_catalog_identity = frozen_catalog_identity(session)
    position_catalog_identity = frozen_position_catalog_identity(session)
    publications = (
        session.query(JDPublication)
        .filter(
            JDPublication.created_at >= window_start,
            JDPublication.created_at <= window_end,
        )
        .order_by(JDPublication.id)
        .all()
    )
    facts = []
    for publication in publications:
        snapshot = dict(publication.snapshot_payload)
        skill_catalog_snapshot = dict(snapshot.get("skill_catalog_snapshot") or {})
        if not skill_catalog_snapshot.get("content_hash"):
            skill_catalog_snapshot.update(skill_catalog_identity)
            snapshot["skill_catalog_snapshot"] = skill_catalog_snapshot
        position_catalog_snapshot = dict(
            snapshot.get("position_catalog_snapshot") or {}
        )
        if not position_catalog_snapshot.get("content_hash"):
            position_catalog_snapshot.update(position_catalog_identity)
            snapshot["position_catalog_snapshot"] = position_catalog_snapshot
        jd, parsed = publication_snapshot_views(snapshot)
        extraction = extraction_to_kg(parsed.extraction_result)
        normalization, _, _ = normalization_to_kg(
            parsed.normalized_result,
            parsed.extraction_result,
            kg_skills=kg_skills,
            kg_positions=kg_positions,
            explicit_skill_mappings=explicit_skill_mappings,
        )
        facts.append(
            map_published_jd_fact_v3(
                jd=jd,
                parsed=parsed,
                extraction_fact=extraction,
                normalized_fact=normalization,
                publication_snapshot=snapshot,
            )
        )
    payload = "".join(
        fact.model_dump_json() + "\n"
        for fact in sorted(
            facts, key=lambda item: (item.source_fact_id, item.source_fact_version)
        )
    ).encode("utf-8")
    compressed = gzip.compress(payload, mtime=0)
    artifact_name = "published-jd-facts.jsonl.gz"
    manifest = ReleaseManifestV1.model_validate(
        {
            "release_schema_version": "kg-release-manifest.v1",
            "release_id": release_id,
            "created_at": datetime.now(window_start.tzinfo),
            "producer": {
                "application": "jobgraph-main-system",
                "git_commit": git_commit,
            },
            "mode": mode,
            "parent_release_id": parent_release_id,
            "observation_window": {"start": window_start, "end": window_end},
            "artifacts": [
                {
                    "artifact_type": "published-jd-facts",
                    "contract_version": "published-jd-fact.v3",
                    "path": artifact_name,
                    "record_count": len(facts),
                }
            ],
        }
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.with_name(output_dir.name + ".staging")
    staging.mkdir()
    (staging / artifact_name).write_bytes(compressed)
    (staging / "manifest.json").write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    staging.replace(output_dir)
    return manifest
