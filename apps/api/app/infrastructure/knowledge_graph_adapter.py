from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.contexts.knowledge_graph import (
    KnowledgeGraphIntegrationConflict,
    KnowledgeGraphIntegrationDisabled,
    KnowledgeGraphIntegrationNotFound,
    KnowledgeGraphIntegrationRuleViolation,
)
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json, thaw_json_object
from app.contexts.knowledge_graph import (
    KnowledgeGraphBuildResult,
    KnowledgeGraphBuildCommand,
    KnowledgeGraphMapping,
    KnowledgeGraphStatus,
    KnowledgeGraphSyncResult,
    KnowledgeGraphUpstream,
    KnowledgeGraphUpstreamResult,
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
)
from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.integrations.knowledge_graph.exceptions import KnowledgeGraphError
from app.integrations.knowledge_graph.mappings import (
    extraction_to_kg,
    normalization_to_kg,
    unique_name_match,
)
from app.integrations.knowledge_graph.published_fact import (
    CONTRACT_VERSION_V3,
    map_published_jd_fact_v3,
    publication_snapshot_views,
)
from app.infrastructure.knowledge_graph_remote import KnowledgeGraphRemoteGateway
from app.infrastructure.knowledge_graph_repositories import (
    SqlAlchemyKnowledgeGraphMappingRepository,
    SqlAlchemyKnowledgeGraphSourceRepository,
)
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.jd_publication import JDPublication
from app.models.standard_position import StandardPosition
from jobgraph_contracts.catalog import StandardSkillSnapshotV2


def _actor(user: Any) -> dict[str, str]:
    actor_id = getattr(user, "actor_id", getattr(user, "account_id", getattr(user, "id", "")))
    return {"actor_id": str(actor_id), "actor_role": user.role}


class KnowledgeGraphAdapter:
    def __init__(self, db: Session, client: KnowledgeGraphClient, *, enabled: bool = True) -> None:
        self._db = db
        self.sources = SqlAlchemyKnowledgeGraphSourceRepository(db)
        self.mappings = SqlAlchemyKnowledgeGraphMappingRepository(db)
        self.remote = KnowledgeGraphRemoteGateway(client)
        self.enabled = enabled

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise KnowledgeGraphIntegrationDisabled("Knowledge graph integration is disabled")

    @staticmethod
    def _proxy(envelope: Any) -> KnowledgeGraphUpstreamResult:
        return KnowledgeGraphUpstreamResult(
            freeze_json(envelope.data),
            KnowledgeGraphUpstream(
                envelope.code,
                envelope.message,
                freeze_json(envelope.details),
                envelope.trace_id,
                freeze_json(envelope.response_headers),
            ),
        )

    def status(self) -> KnowledgeGraphStatus:
        if not self.enabled:
            return KnowledgeGraphStatus("disabled", False)
        envelope = self.remote.readiness()
        return KnowledgeGraphStatus(
            "available", True, freeze_json(envelope.data), envelope.trace_id
        )

    def mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphEntityMapping | None:
        return self.mappings.get(entity_type, main_system_id)

    def _mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphEntityMapping:
        return self.mappings.get_or_create(entity_type, main_system_id)

    def confirmed_skill_mappings(self, payload: dict) -> dict[str, str]:
        """Use the same mapping repository as the editing API for JD sync."""
        result: dict[str, str] = {}
        for item in payload.get("normalized_requirements", []):
            main_skill_id = item.get("skill_id")
            if not main_skill_id:
                continue
            row = self.mapping("skill", str(main_skill_id))
            if row and row.sync_status == "confirmed" and row.knowledge_graph_id:
                result[str(main_skill_id)] = row.knowledge_graph_id
        return result

    def _checkpoint(
        self,
        row: KnowledgeGraphEntityMapping,
        sync_status: str,
        *,
        trace_id: str | None = None,
    ) -> None:
        row.sync_status = sync_status
        row.last_trace_id = trace_id
        row.last_error_code = None
        row.last_error_message = None
        self.mappings.flush(row, refresh=True)

    def _fail(
        self,
        row: KnowledgeGraphEntityMapping,
        exc: KnowledgeGraphError | ValueError,
        stage: str,
    ) -> None:
        self.mappings.record_failure(
            row,
            stage=stage,
            error_code=getattr(exc, "error_code", "contract_mapping_failed"),
            message=str(exc),
            trace_id=getattr(exc, "trace_id", None),
        )

    def sync_jd(
        self,
        document_id: str,
        user: AccountActor | Any,
        *,
        publication_snapshot: dict[str, Any] | None = None,
    ) -> dict:
        self._require_enabled()
        if publication_snapshot is None:
            publication = (
                self._db.query(JDPublication)
                .filter(JDPublication.jd_id == document_id)
                .one_or_none()
            )
            if publication is not None:
                publication_snapshot = dict(publication.snapshot_payload)
                jd, parsed = publication_snapshot_views(publication_snapshot)
            else:
                if self.sources.document(document_id) is None:
                    raise KnowledgeGraphIntegrationNotFound("JD not found")
                raise KnowledgeGraphIntegrationConflict(
                    "position-taxonomy.v3 JD publication is required"
                )
        else:
            jd, parsed = publication_snapshot_views(publication_snapshot)
            if jd.id != document_id:
                raise KnowledgeGraphIntegrationRuleViolation(
                    "jd_publication_document_identity_mismatch"
                )
        if (
            parsed is None
            or not parsed.extraction_result
            or not parsed.normalized_result
            or parsed.workflow_status != "published"
        ):
            raise KnowledgeGraphIntegrationConflict(
                "Only published JD v3 facts can enter authoritative KG sync"
            )
        if parsed.schema_version != "v2" or parsed.normalization_schema_version != "v2":
            raise KnowledgeGraphIntegrationRuleViolation("Only schema_version v2 is supported")
        row = self._mapping("document", document_id)
        try:
            actor = _actor(user)
            main_skill_ids = {
                str(item["skill_id"])
                for item in parsed.normalized_result.get("normalized_requirements", [])
                if item.get("skill_id")
            }
            remote_skills = self.remote.list_skills()
            mapping_revisions = []
            for main_skill_id in sorted(main_skill_ids):
                existing_mapping = self.mapping("skill", main_skill_id)
                if (
                    existing_mapping is not None
                    and existing_mapping.sync_status == "confirmed"
                    and existing_mapping.knowledge_graph_id
                    and existing_mapping.knowledge_graph_id != main_skill_id
                    and existing_mapping.synced_at is not None
                ):
                    mapping_revisions.append(existing_mapping.synced_at)
            for main_skill_id in sorted(main_skill_ids):
                raw_snapshot = self.sources.skill_snapshot(main_skill_id)
                if raw_snapshot is None:
                    raise KnowledgeGraphIntegrationConflict(
                        f"Capability catalog skill not found: {main_skill_id}"
                    )
                try:
                    existing_mapping = self.mapping("skill", main_skill_id)
                    target_skill_id = (
                        existing_mapping.knowledge_graph_id
                        if existing_mapping
                        and existing_mapping.sync_status == "confirmed"
                        and existing_mapping.knowledge_graph_id
                        else main_skill_id
                    )
                    target = next(
                        (
                            item
                            for item in remote_skills
                            if str(item.get("skill_id")) == target_skill_id
                        ),
                        None,
                    )
                    if target is not None and target.get("status", "active") != "active":
                        invalid_mapping = self._mapping("skill", main_skill_id)
                        invalid_mapping.last_error_code = "invalid_skill_mapping_target"
                        invalid_mapping.last_error_message = (
                            f"Knowledge graph skill {target_skill_id} is inactive"
                        )
                        continue
                    snapshot = StandardSkillSnapshotV2.model_validate(
                        {
                            **raw_snapshot,
                            "skill_id": target_skill_id,
                        }
                    )
                except ValueError as exc:
                    raise KnowledgeGraphIntegrationRuleViolation(
                        f"Capability catalog skill snapshot is incomplete: {main_skill_id}"
                    ) from exc
                skill_row = self._mapping("skill", main_skill_id)
                explicit_mapping_confirmed_at = (
                    skill_row.synced_at
                    if skill_row.sync_status == "confirmed"
                    and skill_row.knowledge_graph_id
                    and skill_row.knowledge_graph_id != main_skill_id
                    else None
                )
                snapshot_payload = snapshot.model_dump(mode="json")
                snapshot_version = str(
                    snapshot_payload.get("version")
                    or snapshot_payload.get("catalog_version")
                    or snapshot.skill_id
                )
                if (
                    skill_row.sync_status == "confirmed"
                    and skill_row.sync_version == snapshot_version
                    and target is not None
                    and target.get("status", "active") == "active"
                ):
                    continue
                response = self.remote.upsert_skill_snapshot(
                    snapshot.skill_id,
                    snapshot_payload,
                    **actor,
                )
                skill_row.knowledge_graph_id = snapshot.skill_id
                skill_row.sync_version = snapshot_version
                skill_row.sync_status = "confirmed"
                skill_row.last_trace_id = response.trace_id
                skill_row.synced_at = explicit_mapping_confirmed_at or datetime.now(timezone.utc)
            extraction = extraction_to_kg(parsed.extraction_result)
            positions = self.remote.list_positions()
            skills = self.remote.list_skills()
            explicit_skill_mappings = self.confirmed_skill_mappings(parsed.normalized_result)
            for main_skill_id, target_id in explicit_skill_mappings.items():
                target = next(
                    (
                        skill
                        for skill in skills
                        if str(skill.get("skill_id")) == target_id
                        and skill.get("status", "active") == "active"
                    ),
                    None,
                )
                mapping_row = self.mapping("skill", main_skill_id)
                if target is None and mapping_row is not None:
                    mapping_row.last_error_code = "invalid_skill_mapping_target"
                    mapping_row.last_error_message = (
                        f"Knowledge graph skill {target_id} is missing or inactive"
                    )
            normalization, skill_matches, position_match = normalization_to_kg(
                parsed.normalized_result,
                parsed.extraction_result,
                kg_skills=skills,
                kg_positions=positions,
                explicit_skill_mappings=explicit_skill_mappings,
            )
            mapping_revision_at = max(mapping_revisions, default=None)
            if publication_snapshot is None:
                raise KnowledgeGraphError(
                    "position-taxonomy.v3 publication snapshot is required",
                    status_code=409,
                    error_code="position_v3_publication_snapshot_required",
                )
            published_fact = map_published_jd_fact_v3(
                jd=jd,
                parsed=parsed,
                extraction_fact=extraction,
                normalized_fact=normalization,
                publication_snapshot=publication_snapshot,
                mapping_revision_at=mapping_revision_at,
            ).model_dump(mode="json")
            expected_contract = CONTRACT_VERSION_V3
            sync_version = str(published_fact["source_fact_version"])
            if row.sync_status == "synced" and row.sync_version == sync_version:
                return KnowledgeGraphSyncResult(
                    document_id,
                    row.knowledge_graph_id,
                    sync_version,
                    row.sync_status,
                    True,
                    row.last_trace_id,
                )
            actor = _actor(user)
            response = self.remote.import_published_fact_v3(published_fact, **actor)
            response_contract = (response.data or {}).get("contract_version")
            if response_contract != expected_contract:
                raise KnowledgeGraphError(
                    "Knowledge graph returned an incompatible published fact contract",
                    status_code=502,
                    error_code="knowledge_graph_contract_mismatch",
                    details={
                        "expected": expected_contract,
                        "actual": response_contract,
                    },
                    trace_id=response.trace_id,
                )
            remote_document_id = str((response.data or {}).get("document_id", document_id))
            for main_skill_id, match in skill_matches.items():
                skill_row = self._mapping("skill", main_skill_id)
                skill_row.knowledge_graph_id = str(match["skill_id"])
                skill_row.sync_version = str(match["skill_id"])
                if skill_row.sync_status != "confirmed":
                    skill_row.sync_status = "synced"
                    skill_row.synced_at = datetime.now(timezone.utc)
                skill_row.last_trace_id = response.trace_id
            if position_match and parsed.normalized_result.get("job_classification"):
                classification = parsed.normalized_result["job_classification"]
                classification_key = (
                    classification.get("position_id")
                    or classification.get("position_code")
                    or classification.get("source_title")
                )
                if classification_key:
                    position_row = self._mapping("position", str(classification_key))
                    position_row.knowledge_graph_id = str(position_match["position_id"])
                    position_row.sync_version = str(position_match["position_id"])
                    if position_row.sync_status != "confirmed":
                        position_row.sync_status = "synced"
                        position_row.synced_at = datetime.now(timezone.utc)
                    position_row.last_trace_id = response.trace_id
            self.mappings.mark_synced(
                row,
                remote_id=remote_document_id,
                sync_version=sync_version,
                trace_id=response.trace_id,
            )
            return KnowledgeGraphSyncResult(
                document_id,
                row.knowledge_graph_id,
                sync_version,
                row.sync_status,
                False,
                response.trace_id,
            )
        except (KnowledgeGraphError, ValueError) as exc:
            stage = row.sync_status if row.sync_status != "pending" else "mapping"
            self._fail(row, exc, stage)
            raise

    def resolve_position(self, position_id: str) -> tuple[StandardPosition, str]:
        self._require_enabled()
        position = self.sources.position(position_id)
        if position is None:
            raise KnowledgeGraphIntegrationNotFound("Position not found")
        row = self._mapping("position", position_id)
        if row.knowledge_graph_id and row.sync_status in {"synced", "confirmed"}:
            return position, row.knowledge_graph_id
        match = unique_name_match(self.remote.list_positions(), position.position_name, "name")
        if match is None:
            raise KnowledgeGraphIntegrationConflict(
                "No unique knowledge graph position mapping was found"
            )
        row.knowledge_graph_id = str(match["position_id"])
        row.sync_version = str(match["position_id"])
        row.sync_status = "synced"
        row.synced_at = datetime.now(timezone.utc)
        self.mappings.flush(row)
        return position, row.knowledge_graph_id

    def set_mapping(self, entity_type: str, main_system_id: str, knowledge_graph_id: str) -> dict:
        self._require_enabled()
        if entity_type not in {"position", "skill"}:
            raise KnowledgeGraphIntegrationRuleViolation(
                "Only position and skill mappings can be edited explicitly"
            )
        catalog = (
            self.remote.list_positions() if entity_type == "position" else self.remote.list_skills()
        )
        id_field = "position_id" if entity_type == "position" else "skill_id"
        target = next(
            (item for item in catalog if str(item.get(id_field)) == knowledge_graph_id),
            None,
        )
        if target is None:
            raise KnowledgeGraphIntegrationRuleViolation("Knowledge graph target ID does not exist")
        if target.get("status", "active") != "active":
            raise KnowledgeGraphIntegrationRuleViolation("Knowledge graph target is inactive")
        row = self._mapping(entity_type, main_system_id)
        mapping_changed = (
            row.knowledge_graph_id != knowledge_graph_id or row.sync_status != "confirmed"
        )
        row.knowledge_graph_id = knowledge_graph_id
        row.sync_version = str(knowledge_graph_id)
        row.sync_status = "confirmed"
        row.last_error_code = None
        row.last_error_message = None
        if mapping_changed:
            row.synced_at = datetime.now(timezone.utc)
        if entity_type == "position":
            position = self.sources.position(main_system_id)
            if position is not None:
                position.graph_onboarding_status = "mapped"
        self.mappings.flush(row, refresh=True)
        return mapping_record(row)

    @staticmethod
    def _validate_mapping_entity_type(entity_type: str) -> None:
        if entity_type not in {"position", "skill"}:
            raise KnowledgeGraphIntegrationRuleViolation(
                "Only position and skill mappings can be managed"
            )

    def list_mappings(self, entity_type: str, query: str | None = None, status: str | None = None):
        self._require_enabled()
        self._validate_mapping_entity_type(entity_type)
        rows = {row.main_system_id: row for row in self.mappings.list(entity_type)}
        if entity_type == "position":
            sources = [
                (
                    str(item.id),
                    item.position_name,
                    item.taxonomy_family_code,
                    item.taxonomy_family_name,
                )
                for item in self.sources.positions()
            ]
        else:
            sources = [
                (str(item.id), item.skill_name, None, None) for item in self.sources.skills()
            ]
        needle = (query or "").strip().casefold()
        result = []
        for main_id, name, taxonomy_code, taxonomy_name in sources:
            row = rows.get(main_id)
            item_status = row.sync_status if row is not None else "unmapped"
            if status and item_status != status:
                continue
            if needle and needle not in f"{main_id} {name}".casefold():
                continue
            data = serialize_mapping(row)
            for timestamp_field in ("synced_at", "updated_at"):
                timestamp = data.get(timestamp_field)
                if timestamp is not None:
                    data[timestamp_field] = timestamp.isoformat()
            result.append(
                {
                    **data,
                    "entity_type": entity_type,
                    "main_system_id": main_id,
                    "source_name": name,
                    "source_taxonomy_code": taxonomy_code,
                    "source_taxonomy_name": taxonomy_name,
                    "sync_status": item_status,
                }
            )
        return freeze_json(result)

    def mapping_candidates(self, entity_type: str, query: str | None = None):
        self._require_enabled()
        self._validate_mapping_entity_type(entity_type)
        id_field = "position_id" if entity_type == "position" else "skill_id"
        name_field = "name" if entity_type == "position" else "canonical_name"
        catalog = (
            self.remote.list_positions() if entity_type == "position" else self.remote.list_skills()
        )
        needle = (query or "").strip().casefold()
        result = [
            {
                "entity_type": entity_type,
                "knowledge_graph_id": str(item.get(id_field) or ""),
                "name": str(item.get(name_field) or item.get(id_field) or ""),
                "status": str(item.get("status", "active")),
            }
            for item in catalog
            if not needle
            or needle in f"{item.get(id_field, '')} {item.get(name_field, '')}".casefold()
        ]
        return freeze_json(result[:50])

    def cancel_mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphMapping:
        self._require_enabled()
        self._validate_mapping_entity_type(entity_type)
        row = self.mappings.get(entity_type, main_system_id)
        if row is None or row.knowledge_graph_id is None:
            raise KnowledgeGraphIntegrationNotFound("Mapping not found")
        self.mappings.clear(row)
        if entity_type == "position":
            position = self.sources.position(main_system_id)
            if position is not None:
                position.graph_onboarding_status = "pending"
        return mapping_record(row)

    def retry_mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphMapping:
        self._require_enabled()
        self._validate_mapping_entity_type(entity_type)
        row = self.mappings.get(entity_type, main_system_id)
        if row is None or not row.sync_status.startswith("failed"):
            raise KnowledgeGraphIntegrationConflict("Only failed mappings can be retried")
        if row.knowledge_graph_id:
            return self.set_mapping(entity_type, main_system_id, row.knowledge_graph_id)
        if entity_type == "position":
            self.resolve_position(main_system_id)
            return mapping_record(self.mappings.get(entity_type, main_system_id))
        source = self.sources.skill(main_system_id)
        if source is None:
            raise KnowledgeGraphIntegrationNotFound("Skill not found")
        match = unique_name_match(self.remote.list_skills(), source.skill_name, "canonical_name")
        if match is None:
            raise KnowledgeGraphIntegrationConflict(
                "No unique knowledge graph skill mapping was found"
            )
        return self.set_mapping(entity_type, main_system_id, str(match["skill_id"]))

    def build(
        self,
        position_id: str,
        payload: KnowledgeGraphBuildCommand | dict,
        user: AccountActor | Any,
    ) -> KnowledgeGraphBuildResult:
        _, kg_position_id = self.resolve_position(position_id)
        request_payload = (
            {
                "window_start": (
                    payload.window_start.isoformat() if payload.window_start else None
                ),
                "window_end": (
                    payload.window_end.isoformat() if payload.window_end else None
                ),
                "minimum_effective_weight": payload.minimum_effective_weight,
                "minimum_valid_samples": payload.minimum_valid_samples,
            }
            if isinstance(payload, KnowledgeGraphBuildCommand)
            else payload
        )
        response = self.remote.build_graph(kg_position_id, request_payload, **_actor(user))
        position = self.sources.position(position_id)
        if position is not None:
            position.graph_onboarding_status = "build_created"
        return KnowledgeGraphBuildResult(
            position_id, kg_position_id, freeze_json(response.data), response.trace_id
        )

    def mapping_status(self, document_id: str) -> KnowledgeGraphMapping:
        self._require_enabled()
        return mapping_record(self.mapping("document", document_id))

    def build_runs(self, position_id: str) -> KnowledgeGraphUpstreamResult:
        _, kg_position_id = self.resolve_position(position_id)
        return self._proxy(self.remote.build_runs(kg_position_id))

    def build_run(self, run_id: str) -> KnowledgeGraphUpstreamResult:
        self._require_enabled()
        return self._proxy(self.remote.build_run(run_id))

    def graph(self, position_id: str) -> KnowledgeGraphUpstreamResult:
        _, kg_position_id = self.resolve_position(position_id)
        return self._proxy(self.remote.graph(kg_position_id))

    def versions(self, position_id: str) -> KnowledgeGraphUpstreamResult:
        _, kg_position_id = self.resolve_position(position_id)
        return self._proxy(self.remote.versions(kg_position_id))

    def relation_evidence(self, relation_id: str) -> KnowledgeGraphUpstreamResult:
        self._require_enabled()
        return self._proxy(self.remote.relation_evidence(relation_id))

    def _portal_position_id(self, main_position_id: str | None) -> str:
        if not main_position_id:
            raise KnowledgeGraphIntegrationRuleViolation("Position ID is required")
        _, knowledge_graph_id = self.resolve_position(main_position_id)
        return knowledge_graph_id

    def _portal_evidence(self, envelope: Any) -> Any:
        supports = envelope.data or []
        if not isinstance(supports, list):
            raise KnowledgeGraphIntegrationRuleViolation(
                "Knowledge graph evidence response must be a list"
            )
        combined = []
        for support in supports:
            if not isinstance(support, dict):
                raise KnowledgeGraphIntegrationRuleViolation(
                    "Knowledge graph evidence item must be an object"
                )
            document_id = str(support.get("document_id") or "")
            evidence = support.get("evidence") or {}
            start = evidence.get("start")
            end = evidence.get("end")
            quote = evidence.get("quote")
            raw_text = self.sources.document_text(document_id)
            if raw_text is None:
                raise KnowledgeGraphIntegrationNotFound(
                    f"Evidence source JD not found: {document_id}"
                )
            if not isinstance(start, int) or not isinstance(end, int):
                raise KnowledgeGraphIntegrationConflict(
                    f"Evidence coordinates are missing: {document_id}"
                )
            if start < 0 or end < start or end > len(raw_text) or raw_text[start:end] != quote:
                raise KnowledgeGraphIntegrationConflict(
                    f"Evidence coordinates do not match the authoritative JD: {document_id}"
                )
            combined.append(
                {**support, "source": {"document_id": document_id, "raw_text": raw_text}}
            )
        return combined

    def portal(
        self, command: KnowledgeGraphPortalCommand, actor: AccountActor
    ) -> KnowledgeGraphUpstreamResult:
        self._require_enabled()
        operation = command.operation
        actor_headers = _actor(actor)
        payload = thaw_json_object(command.payload) if command.payload is not None else {}
        params = thaw_json_object(command.params) if command.params is not None else {}

        if operation == KnowledgeGraphPortalOperation.MODIFY_RELATION:
            main_position_id = str(payload.get("position_id") or "")
            payload["position_id"] = self._portal_position_id(main_position_id)

        if operation == KnowledgeGraphPortalOperation.LIST_POSITIONS:
            envelope = self.remote.portal_call("GET", "/api/v1/positions", **actor_headers)
            mappings = {
                str(row.knowledge_graph_id): row.main_system_id
                for row in self.mappings.list_confirmed("position")
            }
            positions = []
            for item in envelope.data or []:
                knowledge_graph_id = str(item.get("position_id") or "")
                main_id = mappings.get(knowledge_graph_id)
                if main_id:
                    positions.append(
                        {**item, "position_id": main_id, "knowledge_graph_id": knowledge_graph_id}
                    )
            envelope.data = positions
            return self._proxy(envelope)

        position_operations = {
            KnowledgeGraphPortalOperation.POSITION,
            KnowledgeGraphPortalOperation.GRAPH,
            KnowledgeGraphPortalOperation.REQUIREMENT_INFLATION,
            KnowledgeGraphPortalOperation.RELATIONS,
            KnowledgeGraphPortalOperation.OPEN_DRAFT,
            KnowledgeGraphPortalOperation.BUILD_RUNS,
            KnowledgeGraphPortalOperation.VERSIONS,
            KnowledgeGraphPortalOperation.VERSION,
            KnowledgeGraphPortalOperation.VERSION_DIFF,
            KnowledgeGraphPortalOperation.ROLLBACK,
            KnowledgeGraphPortalOperation.EVOLUTION_EVENTS,
            KnowledgeGraphPortalOperation.EVOLUTION_EVENT,
            KnowledgeGraphPortalOperation.CAPABILITY_EVOLUTION,
        }
        kg_position_id = (
            self._portal_position_id(command.position_id)
            if operation in position_operations
            else None
        )
        routes = {
            KnowledgeGraphPortalOperation.POSITION: ("GET", f"/api/v1/positions/{kg_position_id}"),
            KnowledgeGraphPortalOperation.GRAPH: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph",
            ),
            KnowledgeGraphPortalOperation.REQUIREMENT_INFLATION: (
                "GET",
                f"/api/v1/position-profiles/{kg_position_id}",
            ),
            KnowledgeGraphPortalOperation.RELATIONS: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/relations",
            ),
            KnowledgeGraphPortalOperation.RELATION_EXPLANATION: (
                "GET",
                f"/api/v1/relations/{command.resource_id}/explanation",
            ),
            KnowledgeGraphPortalOperation.OPEN_DRAFT: (
                "POST",
                f"/api/v1/positions/{kg_position_id}/graph/drafts",
            ),
            KnowledgeGraphPortalOperation.DRAFT_GRAPH: (
                "GET",
                f"/api/v1/graph/build-runs/{command.resource_id}/graph",
            ),
            KnowledgeGraphPortalOperation.MODIFY_RELATION: (
                "POST",
                f"/api/v1/relations/{command.resource_id}/modify",
            ),
            KnowledgeGraphPortalOperation.BUILD_RUNS: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/build-runs",
            ),
            KnowledgeGraphPortalOperation.BUILD_RUN: (
                "GET",
                f"/api/v1/graph/build-runs/{command.resource_id}",
            ),
            KnowledgeGraphPortalOperation.BUILD_JOB: (
                "GET",
                f"/api/v1/graph/build-jobs/{command.resource_id}",
            ),
            KnowledgeGraphPortalOperation.RETRY_BUILD_JOB: (
                "POST",
                f"/api/v1/graph/build-jobs/{command.resource_id}/retry",
            ),
            KnowledgeGraphPortalOperation.BUILD_SAMPLES: (
                "GET",
                f"/api/v1/graph/build-runs/{command.resource_id}/samples",
            ),
            KnowledgeGraphPortalOperation.PUBLISH_GATE: (
                "GET",
                f"/api/v1/graph/build-runs/{command.resource_id}/publish-gate",
            ),
            KnowledgeGraphPortalOperation.PUBLISH: (
                "POST",
                f"/api/v1/graph/build-runs/{command.resource_id}/publish",
            ),
            KnowledgeGraphPortalOperation.AUTO_REVIEW: (
                "POST",
                f"/api/v1/graph/build-runs/{command.resource_id}/auto-review",
            ),
            KnowledgeGraphPortalOperation.UNRESOLVED: (
                "GET",
                "/api/v1/normalization/unresolved-items",
            ),
            KnowledgeGraphPortalOperation.RESOLVE_UNRESOLVED: (
                "POST",
                f"/api/v1/normalization/unresolved-items/{command.resource_id}/{command.action}",
            ),
            KnowledgeGraphPortalOperation.REVIEW_TASKS: ("GET", "/api/v1/review-tasks"),
            KnowledgeGraphPortalOperation.REVIEW_TASK: (
                "GET",
                f"/api/v1/review-tasks/{command.resource_id}",
            ),
            KnowledgeGraphPortalOperation.REVIEW_ACTION: (
                "POST",
                f"/api/v1/review-tasks/{command.resource_id}/{command.action}",
            ),
            KnowledgeGraphPortalOperation.REVIEW_BATCH: ("POST", "/api/v1/review-tasks/batch"),
            KnowledgeGraphPortalOperation.VERSIONS: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions",
            ),
            KnowledgeGraphPortalOperation.VERSION: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions/{command.resource_id}",
            ),
            KnowledgeGraphPortalOperation.VERSION_DIFF: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions/diff",
            ),
            KnowledgeGraphPortalOperation.ROLLBACK: (
                "POST",
                f"/api/v1/positions/{kg_position_id}/graph/versions/{command.resource_id}/rollback",
            ),
            KnowledgeGraphPortalOperation.AGGREGATE_EVIDENCE: (
                "GET",
                f"/api/v1/{command.kind}/{command.resource_id}/evidence",
            ),
            KnowledgeGraphPortalOperation.RELATION_EVIDENCE: (
                "GET",
                f"/api/v1/relations/{command.resource_id}/evidence",
            ),
            KnowledgeGraphPortalOperation.EVOLUTION_EVENTS: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions/evolution-events",
            ),
            KnowledgeGraphPortalOperation.EVOLUTION_EVENT: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions/evolution-events/{command.resource_id}",
            ),
            KnowledgeGraphPortalOperation.CAPABILITY_EVOLUTION: (
                "GET",
                f"/api/v1/positions/{kg_position_id}/graph/versions/capability-evolution",
            ),
        }
        try:
            method, path = routes[operation]
        except KeyError as exc:
            raise KnowledgeGraphIntegrationRuleViolation(
                f"Unsupported portal operation: {operation.value}"
            ) from exc
        envelope = self.remote.portal_call(
            method,
            path,
            payload=payload or None,
            params=params or None,
            **actor_headers,
        )
        if operation == KnowledgeGraphPortalOperation.GRAPH and isinstance(envelope.data, dict):
            version_id = envelope.data.get("version_id")
            envelope.data = {
                **envelope.data,
                "graph_version": str(version_id) if version_id is not None else None,
            }
        if (
            operation == KnowledgeGraphPortalOperation.REQUIREMENT_INFLATION
            and isinstance(envelope.data, dict)
        ):
            envelope.data = {
                "position_id": command.position_id,
                "graph_version": envelope.data.get("graph_version"),
                "graph_version_id": envelope.data.get("graph_version_id"),
                "requirement_inflation": envelope.data.get(
                    "requirement_inflation"
                ),
            }
        if operation == KnowledgeGraphPortalOperation.RELATION_EVIDENCE:
            envelope.data = self._portal_evidence(envelope)
        if operation in {
            KnowledgeGraphPortalOperation.GRAPH,
            KnowledgeGraphPortalOperation.RELATIONS,
            KnowledgeGraphPortalOperation.VERSION,
            KnowledgeGraphPortalOperation.VERSION_DIFF,
            KnowledgeGraphPortalOperation.DRAFT_GRAPH,
        }:
            # Large snapshot payloads do not benefit from the BFF freeze/thaw
            # round trip; keep the raw JSON tree for the portal response.
            return KnowledgeGraphUpstreamResult(
                envelope.data,
                KnowledgeGraphUpstream(
                    envelope.code,
                    envelope.message,
                    freeze_json(envelope.details),
                    envelope.trace_id,
                    freeze_json(envelope.response_headers),
                ),
            )
        return self._proxy(envelope)


def serialize_mapping(row: KnowledgeGraphEntityMapping | None) -> dict:
    if row is None:
        return {"sync_status": "not_synced"}
    return {
        "entity_type": row.entity_type,
        "main_system_id": row.main_system_id,
        "knowledge_graph_id": row.knowledge_graph_id,
        "sync_version": row.sync_version,
        "sync_status": row.sync_status,
        "last_error_code": row.last_error_code,
        "last_error_message": row.last_error_message,
        "last_trace_id": row.last_trace_id,
        "synced_at": row.synced_at,
        "updated_at": row.updated_at,
    }


def mapping_record(row: KnowledgeGraphEntityMapping | None) -> KnowledgeGraphMapping:
    if row is None:
        return KnowledgeGraphMapping(sync_status="not_synced")
    return KnowledgeGraphMapping(
        sync_status=row.sync_status,
        entity_type=row.entity_type,
        main_system_id=row.main_system_id,
        knowledge_graph_id=row.knowledge_graph_id,
        sync_version=row.sync_version,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        last_trace_id=row.last_trace_id,
        synced_at=row.synced_at,
        updated_at=row.updated_at,
    )
