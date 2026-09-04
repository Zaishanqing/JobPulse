from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.positions import (
    PositionRuleViolation,
    require_position_admin,
)
from app.contexts.catalog._ports.positions import (
    PositionChanges,
    PositionDraft,
    PositionRecord,
    PositionUnitOfWork,
)
from app.profile_index_events import PLATFORM_PUBLIC_TENANT_REF, profile_index_event


class PositionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class PositionCatalogDomain:
    code: str
    name: str


@dataclass(frozen=True)
class PositionCatalogPage:
    items: tuple[PositionRecord, ...]
    total: int
    page: int
    page_size: int
    domains: tuple[PositionCatalogDomain, ...]
    jd_counts: Mapping[str, int]


@dataclass(frozen=True)
class ManagePositions:
    uow_factory: Callable[[], PositionUnitOfWork]
    vector_index_enabled: bool = True

    def list_catalog(
        self,
        *,
        search: str = "",
        domain: str = "",
        sort: str = "name",
        order: str = "asc",
        page: int = 1,
        page_size: int = 10,
    ) -> PositionCatalogPage:
        with self.uow_factory() as uow:
            records = uow.positions.list_positions()
            jd_counts = uow.positions.jd_counts_by_position()
        active = [item for item in records if item.lifecycle_status == "active"]
        needle = search.strip().casefold()
        if needle:
            active = [
                item
                for item in active
                if needle in item.position_name.casefold()
                or needle in (item.position_code or "").casefold()
                or needle in item.position_id.casefold()
                or needle in (item.taxonomy_family_name or "").casefold()
                or needle in (item.taxonomy_family_code or "").casefold()
            ]
        domain_key = domain.strip().casefold()
        if domain_key:
            active = [
                item
                for item in active
                if (item.taxonomy_family_code or "").casefold() == domain_key
                or (item.taxonomy_family_name or "").casefold() == domain_key
            ]
        domain_by_code: dict[str, PositionCatalogDomain] = {}
        for item in active:
            code = item.taxonomy_family_code or ""
            name = item.taxonomy_family_name or code or "未分类"
            domain_by_code.setdefault(code, PositionCatalogDomain(code, name))
        domains = tuple(sorted(domain_by_code.values(), key=lambda item: item.name.casefold()))
        if sort == "domain":
            key = lambda item: (
                (item.taxonomy_family_name or item.taxonomy_family_code or "").casefold(),
                item.position_name.casefold(),
            )
        elif sort == "jd_count":
            key = lambda item: (
                jd_counts.get(item.position_id, 0),
                item.position_name.casefold(),
            )
        else:
            key = lambda item: (item.position_name.casefold(),)
        active.sort(key=key, reverse=order == "desc")
        total = len(active)
        safe_page = max(1, page)
        safe_page_size = max(1, page_size)
        start = (safe_page - 1) * safe_page_size
        items = tuple(active[start : start + safe_page_size])
        return PositionCatalogPage(
            items,
            total,
            safe_page,
            safe_page_size,
            domains,
            jd_counts,
        )

    def create(self, actor: AccountActor, draft: PositionDraft) -> PositionRecord:
        require_position_admin(actor.role)
        if draft.position_code is not None or draft.status == "catalog":
            raise PositionRuleViolation(
                "Authoritative taxonomy positions are managed by catalog synchronization"
            )
        with self.uow_factory() as uow:
            record = uow.positions.add(draft)
            self._enqueue_position_event(uow, record.position_id, "position_profile_published")
            uow.commit()
            return record

    def list(self) -> list[PositionRecord]:
        with self.uow_factory() as uow:
            return uow.positions.list_positions()

    def get(self, position_id: str) -> PositionRecord:
        with self.uow_factory() as uow:
            record = uow.positions.get(position_id)
        if record is None:
            raise PositionNotFound("Standard position not found")
        return record

    def update(
        self, actor: AccountActor, position_id: str, changes: PositionChanges
    ) -> PositionRecord:
        require_position_admin(actor.role)
        current = self.get(position_id)
        self._assert_mutable(current)
        with self.uow_factory() as uow:
            record = uow.positions.update(position_id, changes)
            self._enqueue_position_event(uow, position_id, "position_profile_updated")
            uow.commit()
            return record

    def delete(self, actor: AccountActor, position_id: str) -> None:
        require_position_admin(actor.role)
        current = self.get(position_id)
        self._assert_mutable(current)
        with self.uow_factory() as uow:
            self._enqueue_position_event(uow, position_id, "position_profile_revoked")
            uow.positions.delete(position_id)
            uow.commit()

    @staticmethod
    def _assert_mutable(record: PositionRecord) -> None:
        if (
            record.position_code is not None
            and record.taxonomy_version == "position-taxonomy.v3.0.0"
        ) or record.status == "catalog":
            raise PositionRuleViolation(
                "Authoritative taxonomy positions are immutable through CRUD APIs"
            )

    def _enqueue_position_event(
        self, uow: PositionUnitOfWork, position_id: str, event_type: str
    ) -> None:
        if not self.vector_index_enabled:
            return
        uow.add_outbox(
            profile_index_event(
                vector_event_type=event_type,
                entity_type="position",
                entity_id=position_id,
                tenant=PLATFORM_PUBLIC_TENANT_REF,
                target_type="standard_position",
            )
        )
