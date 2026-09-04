"""Port for versioned skill relations supplied by a knowledge boundary."""

from typing import Protocol

from app.domain.gaps import SkillTransferPath
from app.domain.skill_relations import SkillRelation


class SkillRelationSource(Protocol):
    def fetch_relations(self, skill_ids: tuple[str, ...]) -> tuple[SkillRelation, ...]: ...


class SkillTransferPathResolver(Protocol):
    def resolve_paths(
        self, path_refs: tuple[str, ...], *, graph_version: str
    ) -> tuple[SkillTransferPath, ...]: ...
