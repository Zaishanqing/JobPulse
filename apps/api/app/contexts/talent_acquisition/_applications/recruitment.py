from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from typing import Callable

from app.domain.accounts import AccountActor, ENTERPRISE_READ_ROLES
from app.domain.recruitment import (
    RecruitmentRuleViolation,
    require_job_manager_role,
    require_job_status,
    require_job_status_transition,
)
from app.contexts.talent_acquisition._ports.recruitment import (
    JobRecord,
    PublishedJobRecord,
    RecruitmentUnitOfWork,
    SkillWeightInput,
    SkillWeightRecord,
)
from app.domain.errors import PermissionDenied
from app.profile_index_events import profile_index_event, tenant_ref


UoWFactory = Callable[[], RecruitmentUnitOfWork]


class JobNotFound(LookupError):
    pass


@dataclass(frozen=True)
class BrowsePublishedJobs:
    uow_factory: UoWFactory

    @staticmethod
    def _authorize(actor: AccountActor) -> None:
        if actor.role != "personal_user":
            raise PermissionDenied("Only personal users can browse published enterprise jobs")

    def list(self, actor: AccountActor) -> list[PublishedJobRecord]:
        self._authorize(actor)
        with self.uow_factory() as uow:
            return uow.jobs.list_published()

    def get(self, actor: AccountActor, job_id: str) -> PublishedJobRecord:
        self._authorize(actor)
        with self.uow_factory() as uow:
            job = uow.jobs.get_published(job_id)
            if job is None:
                raise JobNotFound("Published enterprise job not found")
            return job


@dataclass(frozen=True)
class ManageRecruitmentJobs:
    uow_factory: UoWFactory
    vector_index_enabled: bool = True

    def create(self, actor: AccountActor, values: FrozenJsonObject) -> JobRecord:
        try:
            require_job_manager_role(actor.role)
        except RecruitmentRuleViolation as exc:
            raise PermissionDenied("Only enterprise users can manage enterprise jobs") from exc
        enterprise_id = str(values["enterprise_id"])
        with self.uow_factory() as uow:
            self._authorize_enterprise(uow, actor, enterprise_id, write=True)
            record = uow.jobs.add(values)
            uow.commit()
            return record

    def list(self, actor: AccountActor) -> list[JobRecord]:
        with self.uow_factory() as uow:
            if actor.role in ENTERPRISE_READ_ROLES:
                return uow.jobs.list_all()
            if actor.role == "enterprise_user":
                return uow.jobs.list_for_owner(actor.account_id)
            raise PermissionDenied("No permission to view enterprise jobs")

    def get(self, actor: AccountActor, job_id: str) -> JobRecord:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=False)
            return job

    def update(
        self, actor: AccountActor, job_id: str, changes: FrozenJsonObject
    ) -> JobRecord:
        if changes.get("status") is not None:
            require_job_status(str(changes["status"]))
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=True)
            if changes.get("status") is not None:
                require_job_status_transition(job.status, str(changes["status"]))
            updated = uow.jobs.update(job_id, changes)
            if updated.status == "published":
                self._enqueue_job_event(
                    uow,
                    updated,
                    "position_profile_published"
                    if job.status != "published"
                    else "position_profile_updated",
                )
            elif job.status == "published":
                self._enqueue_job_event(uow, updated, "position_profile_revoked")
            uow.commit()
            return updated

    def delete(self, actor: AccountActor, job_id: str) -> None:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=True)
            if job.status == "published":
                self._enqueue_job_event(uow, job, "position_profile_revoked")
            uow.jobs.delete(job_id)
            uow.commit()

    def change_status(self, actor: AccountActor, job_id: str, status: str) -> JobRecord:
        require_job_status(status)
        return self.update(actor, job_id, {"status": status})

    def change_headcount(
        self, actor: AccountActor, job_id: str, headcount: int
    ) -> tuple[int, JobRecord]:
        current = self.get(actor, job_id)
        return current.headcount, self.update(actor, job_id, {"headcount": headcount})

    def weights(self, actor: AccountActor, job_id: str) -> list[SkillWeightRecord]:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=False)
            return uow.jobs.list_weights(job_id)

    def replace_weights(
        self, actor: AccountActor, job_id: str, weights: list[SkillWeightInput]
    ) -> list[SkillWeightRecord]:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=True)
            records = uow.jobs.replace_weights(job_id, weights)
            if job.status == "published":
                self._enqueue_job_event(uow, job, "position_profile_updated")
            uow.commit()
            return records

    def reset_weights(self, actor: AccountActor, job_id: str) -> int:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=True)
            count = uow.jobs.clear_weights(job_id)
            if job.status == "published":
                self._enqueue_job_event(uow, job, "position_profile_updated")
            uow.commit()
            return count

    def classify_skills(
        self,
        actor: AccountActor,
        job_id: str,
        skill_ids: list[str],
        classification: str,
    ) -> list[SkillWeightRecord]:
        with self.uow_factory() as uow:
            job = self._required(uow, job_id)
            self._authorize_enterprise(uow, actor, job.enterprise_id, write=True)
            unique_ids = list(dict.fromkeys(skill_ids))
            existing = uow.jobs.list_weights(job_id)
            by_id = {item.skill_id: item for item in existing}
            inputs = []
            for item in existing:
                inputs.append(SkillWeightInput(
                    item.skill_id, item.weight,
                    item.skill_id in unique_ids if classification == "required" else item.is_required,
                    item.skill_id in unique_ids if classification == "bonus" else item.is_bonus,
                ))
            for skill_id in unique_ids:
                if skill_id not in by_id:
                    inputs.append(SkillWeightInput(
                        skill_id, 1.0 if classification == "required" else 0.1,
                        classification == "required", classification == "bonus",
                    ))
            records = uow.jobs.replace_weights(job_id, inputs)
            if job.status == "published":
                self._enqueue_job_event(uow, job, "position_profile_updated")
            uow.commit()
            return [
                item for item in records
                if (item.is_required if classification == "required" else item.is_bonus)
            ]

    def _enqueue_job_event(
        self, uow: RecruitmentUnitOfWork, job: JobRecord, event_type: str
    ) -> None:
        if not self.vector_index_enabled:
            return
        uow.add_outbox(
            profile_index_event(
                vector_event_type=event_type,
                entity_type="position",
                entity_id=job.job_id,
                tenant=tenant_ref(job.enterprise_id),
                target_type="enterprise_job",
            )
        )

    @staticmethod
    def _required(uow: RecruitmentUnitOfWork, job_id: str) -> JobRecord:
        job = uow.jobs.get(job_id)
        if job is None:
            raise JobNotFound("Enterprise job not found")
        return job

    @staticmethod
    def _authorize_enterprise(
        uow: RecruitmentUnitOfWork,
        actor: AccountActor,
        enterprise_id: str,
        *,
        write: bool,
    ) -> None:
        owner = uow.jobs.enterprise_owner(enterprise_id)
        if owner is None:
            raise JobNotFound("Enterprise not found")
        if owner == actor.account_id:
            return
        if not write and actor.role in ENTERPRISE_READ_ROLES:
            return
        raise PermissionDenied("No permission for this enterprise")


@dataclass(frozen=True)
class RecruitmentHandlers:
    jobs: ManageRecruitmentJobs
    published_jobs: BrowsePublishedJobs
