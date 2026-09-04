from __future__ import annotations
from app.domain.json_types import FrozenJsonObject
from app.domain.json_types import freeze_json_object

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.governance import require_governance_role
from app.domain.rag import validate_claims
from app.contexts.governance_feedback._ports.governance import (
    EvidenceDraft,
    EvidenceRecord,
    GovernanceUnitOfWork,
    ReviewEventRecord,
    ReviewRecord,
    RagGenerationRecord,
    EvidenceRetrieverPort,
)


class EvidenceNotFound(LookupError):
    pass


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(RuntimeError):
    pass


class ReviewValidationError(ValueError):
    pass


class RagGenerationNotFound(LookupError):
    pass


class RagConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ManageEvidence:
    uow_factory: Callable[[], GovernanceUnitOfWork]

    def create(self, actor: AccountActor, draft: EvidenceDraft) -> EvidenceRecord:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            record = uow.evidence.add(draft)
            uow.commit()
            return record

    def list(self, actor: AccountActor) -> list[EvidenceRecord]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return uow.evidence.list()

    def get(self, actor: AccountActor, evidence_id: str) -> EvidenceRecord:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return self._required(uow, evidence_id)

    def update(
        self, actor: AccountActor, evidence_id: str, changes: FrozenJsonObject
    ) -> EvidenceRecord:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            self._required(uow, evidence_id)
            record = uow.evidence.update(evidence_id, changes)
            uow.commit()
            return record

    def delete(self, actor: AccountActor, evidence_id: str) -> None:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            self._required(uow, evidence_id)
            uow.evidence.delete(evidence_id)
            uow.commit()

    def related(self, object_type: str, object_id: str) -> list[EvidenceRecord]:
        with self.uow_factory() as uow:
            return uow.evidence.related(object_type, object_id)

    @staticmethod
    def _required(uow: GovernanceUnitOfWork, evidence_id: str) -> EvidenceRecord:
        record = uow.evidence.get(evidence_id)
        if record is None:
            raise EvidenceNotFound("Evidence source not found")
        return record


@dataclass(frozen=True)
class ManageReviews:
    uow_factory: Callable[[], GovernanceUnitOfWork]

    def create(
        self,
        actor: AccountActor,
        *,
        object_type: str,
        object_id: str,
        priority: str,
        reason: str | None,
    ) -> ReviewRecord:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            record = uow.reviews.add(object_type, object_id, priority, reason)
            record = uow.reviews.transition(
                record.task_id,
                actor_id=actor.account_id,
                action="create",
                status="pending",
                comment=reason,
                modified_payload={
                    "object_type": object_type,
                    "object_id": object_id,
                    "priority": priority,
                    "reason": reason,
                },
            )
            uow.commit()
            return record

    def list(self, actor: AccountActor) -> list[ReviewRecord]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return uow.reviews.list()

    def list_page(
        self,
        actor: AccountActor,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        task_kind: str | None = None,
        risk_level: str | None = None,
    ) -> tuple[list[tuple[ReviewRecord, FrozenJsonObject]], int]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            if risk_level is None:
                page_records, total = uow.reviews.list_page(
                    page=page,
                    page_size=page_size,
                    status=status,
                    task_kind=task_kind,
                )
                items = [
                    (task, uow.reviews.context(task.task_id))
                    for task in page_records
                ]
                items.sort(key=self._queue_sort_key)
                return items, total
            items: list[tuple[ReviewRecord, FrozenJsonObject]] = []
            for task in uow.reviews.list():
                if status is not None and task.status != status:
                    continue
                if task_kind is not None and task.object_type != task_kind:
                    continue
                context = uow.reviews.context(task.task_id)
                if risk_level is not None and context.get("risk_level") != risk_level:
                    continue
                items.append((task, context))
            items.sort(key=self._queue_sort_key)
            total = len(items)
            start = (page - 1) * page_size
            return items[start : start + page_size], total

    def counts_by_status(self, actor: AccountActor) -> Mapping[str, int]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return uow.reviews.counts_by_status()

    def get(self, actor: AccountActor, task_id: str) -> ReviewRecord:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return self._required(uow, task_id)

    def context(self, actor: AccountActor, task_id: str) -> FrozenJsonObject:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            self._required(uow, task_id)
            try:
                return uow.reviews.context(task_id)
            except LookupError as exc:
                raise ReviewNotFound("Review subject not found") from exc

    def unresolved_skills(self, actor: AccountActor) -> list[FrozenJsonObject]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            return uow.reviews.unresolved_skills()

    def transition(
        self,
        actor: AccountActor,
        task_id: str,
        action: str,
        comment: str | None = None,
        modified_payload: dict | None = None,
    ) -> ReviewRecord:
        require_governance_role(actor)
        if action not in {"claim", "release", "approve", "reject", "modify"}:
            raise ReviewValidationError(f"Unsupported review action: {action}")
        if action == "reject" and not (comment or "").strip():
            raise ReviewValidationError("Rejection reason is required")
        with self.uow_factory() as uow:
            current = self._required(uow, task_id)
            self._validate_transition(uow, actor, current, action, comment)
            record = self._apply_transition(
                uow, actor, current, action, comment, modified_payload
            )
            uow.commit()
            return record

    def batch_transition(
        self,
        actor: AccountActor,
        task_ids: list[str],
        action: str,
        comment: str | None = None,
    ) -> list[ReviewRecord]:
        require_governance_role(actor)
        if action not in {"claim", "approve", "reject"}:
            raise ReviewValidationError(f"Unsupported review action: {action}")
        if action == "reject" and not (comment or "").strip():
            raise ReviewValidationError("Rejection reason is required")
        unique_ids = list(dict.fromkeys(task_ids))
        with self.uow_factory() as uow:
            current = [self._required(uow, task_id) for task_id in unique_ids]
            for task in current:
                self._validate_transition(uow, actor, task, action, comment)
            records = [
                self._apply_transition(uow, actor, task, action, comment, None)
                for task in current
            ]
            uow.commit()
            return records

    @staticmethod
    def _queue_sort_key(item: tuple[ReviewRecord, FrozenJsonObject]) -> tuple:
        task = item[0]
        timestamp = task.created_at.timestamp() if task.created_at else 0.0
        return (task.status not in {"pending", "claimed"}, -timestamp, task.task_id)

    def _validate_transition(
        self,
        uow: GovernanceUnitOfWork,
        actor: AccountActor,
        current: ReviewRecord,
        action: str,
        comment: str | None,
    ) -> None:
        target_status = {
            "claim": "claimed",
            "release": "pending",
            "approve": "approved",
            "reject": "rejected",
            "modify": "modified",
        }[action]
        if current.status == target_status and action != "release":
            if action == "reject" and current.review_comment != comment:
                raise ReviewConflict(
                    "Review task was already rejected with a different reason"
                )
            return
        if action == "claim" and current.status != "pending":
            raise ReviewConflict("Only pending review tasks can be claimed")
        if action == "release":
            if current.status != "claimed":
                raise ReviewConflict("Only claimed review tasks can be released")
            if current.reviewer_id != actor.account_id and actor.role not in {
                "admin",
                "developer",
            }:
                raise ReviewConflict(
                    "Only the claimant or an administrator can release this task"
                )
        elif action != "claim":
            if current.status != "claimed":
                raise ReviewConflict(
                    "Review task must be claimed before it can be "
                    f"{action}d"
                )
            if current.reviewer_id != actor.account_id:
                raise ReviewConflict("Claimed review task belongs to another reviewer")
            if target_status not in {"approved", "rejected", "modified"}:
                raise ReviewConflict(
                    f"Review task cannot be {action}d from its current state"
                )
        if current.object_type == "jd_parse_result":
            if action not in {"claim", "release", "approve", "reject"}:
                raise ReviewConflict(
                    "JD parse review tasks do not support payload modification"
                )
            if action == "approve" and current.status != target_status:
                try:
                    uow.reviews.validate_approve_active(
                        current.object_id,
                        task_id=current.task_id,
                        actor_id=actor.account_id,
                        actor_role=actor.role,
                    )
                except LookupError as exc:
                    raise ReviewNotFound("JD parse result not found") from exc
                except RuntimeError as exc:
                    raise ReviewConflict(str(exc)) from exc

    @staticmethod
    def _apply_transition(
        uow: GovernanceUnitOfWork,
        actor: AccountActor,
        current: ReviewRecord,
        action: str,
        comment: str | None,
        modified_payload: dict | None,
    ) -> ReviewRecord:
        target_status = {
            "claim": "claimed",
            "release": "pending",
            "approve": "approved",
            "reject": "rejected",
            "modify": "modified",
        }[action]
        if current.status == target_status and action != "release":
            return current
        try:
            if current.object_type == "jd_parse_result" and action in {"approve", "reject"}:
                if action == "approve":
                    uow.reviews.approve_active(
                        current.object_id,
                        task_id=current.task_id,
                        actor_id=actor.account_id,
                        actor_role=actor.role,
                        comment=comment,
                    )
                else:
                    uow.reviews.set_jd_parse_review_status(
                        current.object_id,
                        workflow_status="draft",
                        need_review=True,
                    )
            record = (
                uow.reviews.get(current.task_id)
                if current.object_type == "jd_parse_result" and action == "approve"
                else uow.reviews.transition(
                    current.task_id,
                    actor_id=actor.account_id,
                    action=action,
                    status=target_status,
                    comment=comment,
                    modified_payload=modified_payload,
                )
            )
        except LookupError as exc:
            raise ReviewNotFound("Review task not found") from exc
        except RuntimeError as exc:
            raise ReviewConflict(str(exc)) from exc
        if record is None:
            raise ReviewNotFound("Review task not found")
        return record

    def history(self, actor: AccountActor, task_id: str) -> list[ReviewEventRecord]:
        require_governance_role(actor)
        with self.uow_factory() as uow:
            self._required(uow, task_id)
            return uow.reviews.history(task_id)

    @staticmethod
    def _required(uow: GovernanceUnitOfWork, task_id: str) -> ReviewRecord:
        record = uow.reviews.get(task_id)
        if record is None:
            raise ReviewNotFound("Review task not found")
        return record


@dataclass(frozen=True)
class ManageRag:
    uow_factory: Callable[[], GovernanceUnitOfWork]
    retriever: EvidenceRetrieverPort

    @staticmethod
    def _admin(actor: AccountActor) -> None:
        if actor.role not in {"admin", "developer"}:
            from app.domain.errors import PermissionDenied
            raise PermissionDenied("No permission to access RAG management APIs")

    def retrieve(self, actor: AccountActor, query: str, top_k: int) -> FrozenJsonObject:
        self._admin(actor)
        with self.uow_factory() as uow:
            documents = tuple(freeze_json_object(self._document(item)) for item in uow.evidence.list())
        hits = self.retriever.retrieve(query, documents, top_k)
        implementation, provider = self.retriever.metadata()
        return freeze_json_object({"query": query, "results": [{key: value for key, value in hit.items() if key != "text"} for hit in hits], "implementation_status": implementation, "provider": provider, "mock": False})

    def generate(self, actor: AccountActor, prompt: str, evidence_ids: list[str]) -> RagGenerationRecord:
        self._admin(actor)
        with self.uow_factory() as uow:
            evidence = self._evidence(uow, evidence_ids)
            excerpts = [{"evidence_id": item.evidence_id, "title": item.title, "excerpt": (item.raw_text or "")[:240]} for item in evidence]
            text = "\n".join(f"[{index}] {item['title']}：{item['excerpt']}" for index, item in enumerate(excerpts, 1)) if evidence else ""
            record = uow.rag.add(prompt=prompt, text=text, evidence_ids=[item.evidence_id for item in evidence], citations=excerpts, need_review=not evidence, created_by=actor.account_id)
            uow.commit()
            return record

    def get(self, actor: AccountActor, generation_id: str) -> RagGenerationRecord:
        with self.uow_factory() as uow:
            record = uow.rag.get(generation_id)
            if record is None:
                raise RagGenerationNotFound("RAG generation not found")
            if record.created_by != actor.account_id and actor.role not in {"admin", "developer", "reviewer"}:
                from app.domain.errors import PermissionDenied
                raise PermissionDenied("No permission to access RAG generation")
            return record

    def update(self, actor: AccountActor, generation_id: str, text: str) -> RagGenerationRecord:
        record = self.get(actor, generation_id)
        if record.status != "draft":
            raise RagConflict("Confirmed RAG generation is immutable")
        if record.created_by != actor.account_id and actor.role not in {"admin", "developer"}:
            from app.domain.errors import PermissionDenied
            raise PermissionDenied("No permission to edit RAG generation")
        with self.uow_factory() as uow:
            updated = uow.rag.update_text(generation_id, text)
            uow.commit()
            return updated

    def confirm(self, actor: AccountActor, generation_id: str) -> RagGenerationRecord:
        if actor.role not in {"admin", "developer", "reviewer"}:
            from app.domain.errors import PermissionDenied
            raise PermissionDenied("No permission to confirm RAG generation")
        record = self.get(actor, generation_id)
        if record.status != "draft":
            raise RagConflict("RAG generation is already confirmed")
        with self.uow_factory() as uow:
            confirmed = uow.rag.confirm(generation_id, actor.account_id)
            uow.commit()
            return confirmed

    def validate(self, actor: AccountActor, text: str, evidence_ids: list[str], claims: list[str]) -> FrozenJsonObject:
        self._admin(actor)
        claims = claims or [text]
        with self.uow_factory() as uow:
            evidence = self._evidence(uow, evidence_ids)
        evidence_text = " ".join(value for item in evidence for value in (item.title, item.source_name, item.raw_text) if value)
        supported, unsupported = validate_claims(claims, evidence_text, bool(evidence))
        return freeze_json_object({"valid": bool(claims) and not unsupported, "coverage_score": round(len(supported) / len(claims), 2) if claims else 0.0, "unsupported_claims": unsupported, "evidence_ids": [item.evidence_id for item in evidence], "details": {"implementation_status": "rule_based_lexical_evidence_validation", "matched_evidence_count": len(evidence), "supported_claim_count": len(supported), "claim_count": len(claims), "mock": False}})

    def low_evidence(self, actor: AccountActor) -> list[RagGenerationRecord]:
        self._admin(actor)
        with self.uow_factory() as uow:
            return uow.rag.list_need_review()

    @staticmethod
    def _evidence(uow: GovernanceUnitOfWork, evidence_ids: list[str]) -> list[EvidenceRecord]:
        result = []
        for evidence_id in dict.fromkeys(evidence_ids):
            item = uow.evidence.get(evidence_id)
            if item is None:
                raise EvidenceNotFound("Evidence source not found")
            result.append(item)
        return result

    @staticmethod
    def _document(item: EvidenceRecord) -> dict:
        return {"evidence_id": item.evidence_id, "source_type": item.source_type, "source_name": item.source_name, "title": item.title, "url": item.url, "raw_text": item.raw_text, "publish_date": item.publish_date.isoformat() if item.publish_date else None, "credibility_score": item.credibility_score, "related_object_type": item.related_object_type, "related_object_id": item.related_object_id, "text": " ".join(value for value in (item.title, item.source_name, item.raw_text) if value)}


@dataclass(frozen=True)
class GovernanceHandlers:
    evidence: ManageEvidence
    reviews: ManageReviews
    rag: ManageRag
