from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from app.application.mappers import GraphSnapshotCompatibilityMapper
from app.domain.profile_thresholds import (
    DEFAULT_POSITION_PROFILE_THRESHOLDS,
    PositionProfileThresholdConfig,
    apply_position_profile_thresholds,
    classify_requirement_inflation,
    requirement_inflation_risk_level,
    responsibility_key,
)
from app.domain.responsibility_topics import merge_responsibility_topics
from app.infrastructure.sqlalchemy.graph_persistence import _snapshot
from app.infrastructure.sqlalchemy.query_base import QuerySession, evidence_projection
from app.models import (
    DuplicateCluster,
    ExtractionEvidence,
    GraphBuildRun,
    GraphBuildSample,
    GraphVersion,
    GraphVersionDependencyRecord,
    JDDocument,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    Skill,
    StandardPosition,
)


class PositionProfileQueryMixin(QuerySession):
    def skill_relations_batch(self, skill_ids: tuple[str, ...]) -> dict:
        requested = set(skill_ids)
        rows = self.session.scalars(
            select(GraphVersionDependencyRecord)
            .join(
                StandardPosition,
                StandardPosition.current_version_id
                == GraphVersionDependencyRecord.graph_version_id,
            )
            .where(
                (
                    GraphVersionDependencyRecord.prerequisite_skill_id.in_(requested)
                )
                | (GraphVersionDependencyRecord.advanced_skill_id.in_(requested))
            )
            .order_by(GraphVersionDependencyRecord.id)
        ).all()
        relations = [
            {
                "relation_id": str(row.id),
                "source_skill_id": row.prerequisite_skill_id,
                "target_skill_id": row.advanced_skill_id,
                "relation_type": "prerequisite",
                "source_system": "knowledge-graph",
                "graph_version": str(row.graph_version_id),
                "confidence": float((row.metrics or {}).get("confidence", 1.0)),
                "evidence_refs": [str(value) for value in row.evidence_ids],
            }
            for row in rows
        ]
        return {
            "graph_version": "kg-published-current",
            "relations": relations,
        }

    def position_profile(
        self,
        position_id: str,
        *,
        contract_version: str,
        graph_version_id: int | None = None,
        view: str = "published",
        draft_id: int | None = None,
    ) -> dict | None:
        position = self.session.scalar(
            select(StandardPosition).where(
                StandardPosition.position_id == position_id
            )
        )
        if position is None:
            return None
        version = None
        if view == "published":
            version = (
                self.session.get(GraphVersion, graph_version_id)
                if graph_version_id is not None
                else self.current_version(position_id)
            )
            if version is None or version.position_id != position_id:
                return None
            snapshot = GraphSnapshotCompatibilityMapper.to_current(version.snapshot)
            build_run_id = version.build_run_id
        else:
            if draft_id is None:
                raise ValueError("draft_id is required for draft or experimental profile reads")
            run = self.session.get(GraphBuildRun, draft_id)
            if run is None or run.position_id != position_id:
                return None
            if self.session.scalar(
                select(GraphVersion.id).where(GraphVersion.build_run_id == run.id)
            ) is not None:
                raise ValueError("published build runs must be read with view=published")
            relations = self.session.scalars(
                select(PositionSkillRelationDraft).where(
                    PositionSkillRelationDraft.build_run_id == run.id
                )
            ).all()
            snapshot = _snapshot(self.session, run, relations)
            build_run_id = run.id

        profiled_items = (
            list(snapshot.get("responsibilities", []))
            + list(snapshot.get("requirement_profile", []))
        )
        profile_evidence_ids = {
            int(value)
            for item in profiled_items
            if isinstance(item, dict)
            for value in item.get("evidence_ids", [])
            if str(value).isdigit()
        }
        profile_evidence = {
            evidence.id: evidence_projection(evidence)
            for evidence in self.session.scalars(
                select(ExtractionEvidence).where(
                    ExtractionEvidence.id.in_(profile_evidence_ids)
                )
            ).all()
        } if profile_evidence_ids else {}
        for item in profiled_items:
            if not isinstance(item, dict):
                continue
            item["evidence"] = [
                profile_evidence[int(value)]
                for value in item.get("evidence_ids", [])
                if str(value).isdigit()
                and int(value) in profile_evidence
            ]

        run = self.session.get(GraphBuildRun, build_run_id)
        config = run.config_snapshot if run else {}
        thresholds = PositionProfileThresholdConfig.from_serialized(
            config.get("position_profile_thresholds")
            or DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized()
        )
        samples = self.session.scalars(
            select(GraphBuildSample).where(
                GraphBuildSample.build_run_id == build_run_id,
                GraphBuildSample.included.is_(True),
            )
        ).all()
        frozen_deduplication = config.get("document_deduplication")
        if isinstance(frozen_deduplication, dict) and frozen_deduplication:

            def dedup_key(document_id: str) -> str:
                return str(
                    frozen_deduplication.get(
                        document_id, f"document:{document_id}"
                    )
                )

        else:
            clusters = self.session.scalars(select(DuplicateCluster)).all()
            cluster_key_by_document = {
                document_id: cluster.cluster_key
                for cluster in clusters
                for document_id in cluster.document_ids
            }

            def dedup_key(document_id: str) -> str:
                return cluster_key_by_document.get(
                    document_id, f"document:{document_id}"
                )

        included_document_ids = {sample.document_id for sample in samples}
        total_dedup_jd_count = len(
            {dedup_key(document_id) for document_id in included_document_ids}
        )
        supports = self.session.scalars(
            select(PositionSkillSupport)
            .where(PositionSkillSupport.build_run_id == build_run_id)
            .order_by(PositionSkillSupport.id)
        ).all()
        skill_supporting_jd_count: dict[str, int] = defaultdict(int)
        skill_required_jd_count: dict[str, int] = defaultdict(int)
        skill_support_documents: dict[str, set[str]] = defaultdict(set)
        skill_required_documents: dict[str, set[str]] = defaultdict(set)
        skill_support_document_ids: dict[str, set[str]] = defaultdict(set)
        evidence_summary = []
        evidence_count_by_skill: dict[str, int] = {}
        for support in supports:
            skill_document_key = dedup_key(support.document_id)
            skill_support_documents[support.skill_id].add(skill_document_key)
            skill_support_document_ids[support.skill_id].add(support.document_id)
            if support.modality == "required":
                skill_required_documents[support.skill_id].add(skill_document_key)
            evidence = self.session.get(ExtractionEvidence, support.evidence_id)
            evidence_count_by_skill[support.skill_id] = (
                evidence_count_by_skill.get(support.skill_id, 0) + 1
            )
            evidence_summary.append(
                {
                    "evidence_id": support.evidence_id,
                    "document_id": support.document_id,
                    "requirement_id": support.requirement_id,
                    "skill_id": support.skill_id,
                    "quote": evidence.quote if evidence else None,
                    "alignment": evidence.alignment if evidence else None,
                }
            )
        for skill_id, document_keys in skill_support_documents.items():
            skill_supporting_jd_count[skill_id] = len(document_keys)
        for skill_id, document_keys in skill_required_documents.items():
            skill_required_jd_count[skill_id] = len(document_keys)
        documents = self.session.scalars(
            select(JDDocument).where(
                JDDocument.document_id.in_(included_document_ids)
            )
        ).all()
        document_by_id = {item.document_id: item for item in documents}
        skill_enterprises: dict[str, set[str]] = defaultdict(set)
        skill_sources: dict[str, set[str]] = defaultdict(set)
        for skill_id, document_ids in skill_support_document_ids.items():
            for document_id in document_ids:
                document = document_by_id.get(document_id)
                if document is None:
                    continue
                if document.enterprise_name:
                    skill_enterprises[skill_id].add(document.enterprise_name)
                if document.source_name:
                    skill_sources[skill_id].add(document.source_name)
        skill_enterprise_count = {
            skill_id: len(values) for skill_id, values in skill_enterprises.items()
        }
        skill_source_count = {
            skill_id: len(values) for skill_id, values in skill_sources.items()
        }
        skill_ids_by_evidence_id: dict[int, set[str]] = defaultdict(set)
        for support in supports:
            skill_ids_by_evidence_id[support.evidence_id].add(support.skill_id)
        relation_names = {
            str(item.get("skill_id") or ""): str(
                item.get("canonical_name") or item.get("skill_name") or ""
            )
            for item in snapshot.get("skill_relations", [])
            if isinstance(item, dict) and item.get("skill_id")
        }
        enriched_responsibilities = []
        for item in snapshot.get("responsibilities", []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            document_ids = {str(value) for value in item.get("document_ids") or ()}
            linked_skill_ids = {
                skill_id
                for value in item.get("evidence_ids") or ()
                if str(value).isdigit()
                for skill_id in skill_ids_by_evidence_id.get(int(value), set())
            }
            linked_skill_ids.update(
                skill_id
                for skill_id, skill_name in relation_names.items()
                if skill_name
                and skill_name.casefold() in text.casefold()
                and document_ids & skill_support_document_ids.get(skill_id, set())
            )
            enriched_responsibilities.append(
                {**item, "skill_ids": sorted(linked_skill_ids)}
            )
        profile_responsibilities = merge_responsibility_topics(
            enriched_responsibilities
        )
        for item in profile_responsibilities:
            group_document_ids = {
                str(value) for value in item.get("document_ids") or ()
            }
            contextual_support_threshold = max(
                2, (len(group_document_ids) + 2) // 3
            )
            item["skill_ids"] = sorted(
                {
                    *(str(value) for value in item.get("skill_ids") or ()),
                    *(
                        skill_id
                        for skill_id, support_document_ids in skill_support_document_ids.items()
                        if len(group_document_ids & support_document_ids)
                        >= contextual_support_threshold
                    ),
                }
            )
        responsibility_supporting_jd_count: dict[str, int] = {}
        for item in profile_responsibilities:
            document_ids = item.get("document_ids") or []
            responsibility_supporting_jd_count[responsibility_key(item)] = len(
                {dedup_key(document_id) for document_id in document_ids}
            )
        relations = snapshot.get("skill_relations", [])
        filtered = apply_position_profile_thresholds(
            relations,
            profile_responsibilities,
            skill_supporting_jd_count=skill_supporting_jd_count,
            skill_required_jd_count=skill_required_jd_count,
            responsibility_supporting_jd_count=responsibility_supporting_jd_count,
            total_dedup_jd_count=total_dedup_jd_count,
            thresholds=thresholds,
            skill_enterprise_count=skill_enterprise_count,
            skill_source_count=skill_source_count,
        )
        relation_by_skill = {
            str(item.get("skill_id") or ""): item for item in relations
        }
        missing_skill_ids = {
            support.skill_id for support in supports
        } - set(relation_by_skill)
        skill_name_by_id = {
            item.skill_id: item.canonical_name
            for item in self.session.scalars(
                select(Skill).where(Skill.skill_id.in_(missing_skill_ids))
            ).all()
        }
        required_support_by_document_skill = {}
        for support in supports:
            if support.modality == "required":
                required_support_by_document_skill.setdefault(
                    (support.document_id, support.skill_id), support
                )
        diagnostics_by_document: dict[str, list[dict]] = defaultdict(list)
        status_counts: Counter[str] = Counter()
        denominator = max(total_dedup_jd_count, 1)
        for (document_id, skill_id), support in sorted(
            required_support_by_document_skill.items()
        ):
            supporting_count = int(skill_supporting_jd_count.get(skill_id, 0))
            required_count = int(skill_required_jd_count.get(skill_id, 0))
            required_prevalence = required_count / denominator
            required_purity = (
                required_count / supporting_count if supporting_count else 0.0
            )
            calibration = classify_requirement_inflation(
                modality="required",
                supporting_jd_count=supporting_count,
                required_supporting_jd_count=required_count,
                required_prevalence=required_prevalence,
                required_purity=required_purity,
                enterprise_count=int(skill_enterprise_count.get(skill_id, 0)),
                gate=thresholds.required_skill_gate,
            )
            current_document = document_by_id.get(document_id)
            current_enterprise = (
                current_document.enterprise_name if current_document else None
            )
            current_source = current_document.source_name if current_document else None
            current_document_key = dedup_key(document_id)
            other_support_documents = [
                document_by_id[other_document_id]
                for other_document_id in skill_support_document_ids.get(skill_id, set())
                if dedup_key(other_document_id) != current_document_key
                and other_document_id in document_by_id
            ]
            leave_one_out_enterprises = {
                item.enterprise_name
                for item in other_support_documents
                if item.enterprise_name
                and (
                    current_enterprise is None
                    or item.enterprise_name != current_enterprise
                )
            }
            leave_one_out_sources = {
                item.source_name
                for item in other_support_documents
                if item.source_name
                and (current_source is None or item.source_name != current_source)
            }
            relation = relation_by_skill.get(skill_id, {})
            diagnostics_by_document[document_id].append(
                {
                    "requirement_id": support.requirement_id,
                    "skill_id": skill_id,
                    "skill_name": str(
                        relation.get("canonical_name")
                        or skill_name_by_id.get(skill_id)
                        or skill_id
                    ),
                    "evidence_id": support.evidence_id,
                    "jd_modality": "required",
                    "market_status": calibration.status,
                    "inflation_risk": calibration.inflation_risk,
                    "reason_codes": list(calibration.reason_codes),
                    "market": {
                        "support_ratio": round(supporting_count / denominator, 4),
                        "supporting_jd_count": supporting_count,
                        "required_supporting_jd_count": required_count,
                        "required_prevalence": round(required_prevalence, 4),
                        "required_purity": round(required_purity, 4),
                        "enterprise_count": int(
                            skill_enterprise_count.get(skill_id, 0)
                        ),
                        "source_count": int(skill_source_count.get(skill_id, 0)),
                        "leave_one_out_required_jd_count": len(
                            skill_required_documents.get(skill_id, set())
                            - {current_document_key}
                        ),
                        "leave_one_out_enterprise_count": len(
                            leave_one_out_enterprises
                        ),
                        "leave_one_out_source_count": len(leave_one_out_sources),
                    },
                }
            )
            status_counts[calibration.status] += 1
        jd_diagnostics = []
        jd_risk_level_counts: Counter[str] = Counter()
        for document_id, items in sorted(diagnostics_by_document.items()):
            inflation_risk_count = sum(
                item["inflation_risk"] is True for item in items
            )
            inflation_ratio = inflation_risk_count / max(len(items), 1)
            risk_level = requirement_inflation_risk_level(inflation_ratio)
            jd_risk_level_counts[risk_level] += 1
            document = document_by_id.get(document_id)
            jd_diagnostics.append(
                {
                    "document_id": document_id,
                    "enterprise_name": document.enterprise_name if document else None,
                    "source_name": document.source_name if document else None,
                    "required_skill_count": len(items),
                    "inflation_risk_skill_count": inflation_risk_count,
                    "inflation_ratio": round(inflation_ratio, 4),
                    "risk_level": risk_level,
                    "requirements": items,
                }
            )
        requirement_inflation = {
            "algorithm_version": "requirement-strength-calibration.v1",
            "scope": "required_skills",
            "summary": {
                "jd_count": len(jd_diagnostics),
                "total_required_requirement_count": sum(status_counts.values()),
                "market_supported_count": status_counts["market_supported"],
                "enterprise_specific_count": status_counts["enterprise_specific"],
                "inflation_risk_count": status_counts["inflation_risk"],
                "jd_risk_level_counts": {
                    level: jd_risk_level_counts[level]
                    for level in ("low", "medium", "high")
                },
            },
            "jd_diagnostics": jd_diagnostics,
        }
        retained_skill_ids = {
            str(item.get("skill_id") or "")
            for item in filtered.skill_relations
        }
        evidence_summary = [
            item
            for item in evidence_summary
            if item.get("skill_id") in retained_skill_ids
        ]
        taxonomy_versions = sorted(
            {
                str(item["taxonomy_version"])
                for item in filtered.skill_relations
                if item.get("taxonomy_version")
            }
        )
        skill_taxonomy_version = (
            version.skill_catalog_version
            if version is not None
            else taxonomy_versions[0]
            if len(taxonomy_versions) == 1
            else "taxonomy-set"
            if taxonomy_versions
            else "absent"
        )
        sample_stats = snapshot.get("sample_stats") or {}
        quality = {
            "included_samples": int(sample_stats.get("included_samples", 0)),
            "excluded_samples": int(sample_stats.get("excluded_samples", 0)),
            "relation_count": len(filtered.skill_relations),
            "evidence_count": len(evidence_summary),
            "unresolved_count": int(sample_stats.get("unresolved_count", 0)),
            "non_exact_evidence_count": sum(
                item["alignment"] not in ("exact", "normalized_exact")
                for item in evidence_summary
            ),
            "publication_gate_passed": version is not None,
            "skill_retained_by_level": filtered.skill_retained_by_level,
            "skill_filtered_by_level": filtered.skill_filtered_by_level,
            "responsibility_retained_by_level": (
                filtered.responsibility_retained_by_level
            ),
            "responsibility_filtered_by_level": (
                filtered.responsibility_filtered_by_level
            ),
        }
        result = {
            "contract_version": contract_version,
            "position_id": position_id,
            "position_name": position.name,
            "graph_version": version.version_name if version else f"{view}:{draft_id}",
            "profile_state": view,
            "taxonomy_version": position.taxonomy_version or "absent",
            "responsibilities": list(filtered.responsibilities),
            "requirements": list(snapshot.get("requirement_profile", [])),
            "skill_relations": [
                {
                    "skill_id": str(item["skill_id"]),
                    "skill_name": str(item.get("canonical_name") or item["skill_id"]),
                    "category_code": item.get("category_code"),
                    "taxonomy_version": item.get("taxonomy_version"),
                    "weight": float(item.get("final_weight", item.get("weight", 0))),
                    "confidence": float(
                        item.get("final_confidence", item.get("confidence", 0))
                    ),
                    "importance_level": str(
                        item.get("final_importance_level", item.get("importance_level", ""))
                    ),
                    "modality": item.get("primary_modality"),
                    "profile_tier": item.get("profile_tier"),
                    "support_ratio": item.get("support_ratio"),
                    "supporting_jd_count": item.get("supporting_jd_count"),
                    "required_supporting_jd_count": item.get(
                        "required_supporting_jd_count"
                    ),
                    "required_prevalence": item.get("required_prevalence"),
                    "required_purity": item.get("required_purity"),
                    "enterprise_count": item.get("enterprise_count", 0),
                    "source_count": item.get("source_count", 0),
                    "requirement_market_status": item.get(
                        "requirement_market_status", "not_applicable"
                    ),
                    "inflation_risk": item.get("inflation_risk", False),
                    "inflation_reason_codes": item.get(
                        "inflation_reason_codes", []
                    ),
                    "evidence_count": evidence_count_by_skill.get(str(item["skill_id"]), 0),
                }
                for item in filtered.skill_relations
            ],
            "evidence_summary": evidence_summary,
            "quality": quality,
            "requirement_inflation": requirement_inflation,
        }
        if contract_version in {"position-profile.v2", "position-profile.v3"}:
            if version is None:
                # Draft/experimental contracts are explicit but still need stable coordinates.
                run = self.session.get(GraphBuildRun, draft_id)
                config = run.config_snapshot if run else {}
                result.update(
                    {
                        "graph_version_id": draft_id,
                        "published_at": None,
                        "dependencies": self._draft_dependencies(run, config),
                    }
                )
            else:
                result.update(
                    {
                        "graph_version_id": version.id,
                        "published_at": version.published_at,
                        "dependencies": {
                            "published_fact_versions": list(version.published_fact_versions),
                            "skill_catalog_version": skill_taxonomy_version,
                            "mapping_snapshot_version": version.mapping_snapshot_version,
                            "normalization_algorithm_version": version.normalization_algorithm_version,
                            "build_config_version": version.build_config_version,
                            "source_time_window": dict(version.source_time_window),
                            "position_profile_thresholds": config.get(
                                "position_profile_thresholds"
                            )
                            or DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized(),
                        },
                    }
                )
        if contract_version == "position-profile.v3":
            if (
                position.status != "active"
                or position.position_code != position_id
                or position.taxonomy_version != "position-taxonomy.v3.0.0"
                or position.sample_support_status != "sufficient"
            ):
                return None
            result.update(
                {
                    "position_code": position.position_code,
                    "classification_status": "resolved",
                    "career_level": None,
                    "leadership_scope": None,
                    "sample_support_status": position.sample_support_status,
                }
            )
        return result

    @staticmethod
    def _draft_dependencies(run: GraphBuildRun, config: dict) -> dict:
        return {
            "published_fact_versions": list(config.get("published_fact_versions", [])),
            "skill_catalog_version": config.get("skill_catalog_version", "absent"),
            "mapping_snapshot_version": config.get("mapping_snapshot_version", "absent"),
            "normalization_algorithm_version": config.get(
                "normalization_algorithm_version", "legacy-unspecified"
            ),
            "build_config_version": config.get("build_config_version", "legacy-unspecified"),
            "position_profile_thresholds": config.get(
                "position_profile_thresholds"
            )
            or DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized(),
            "source_time_window": {
                "start": run.window_start.isoformat() if run and run.window_start else None,
                "end": run.window_end.isoformat() if run and run.window_end else None,
            },
        }
