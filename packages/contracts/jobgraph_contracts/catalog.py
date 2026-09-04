from typing import Literal

from pydantic import Field

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.skill_taxonomy import SkillClassificationV1


class StandardSkillRef(StrictContract):
    skill_id: str
    canonical_name: str
    category_code: str | None = None
    aliases: list[str] = Field(default_factory=list)


class StandardSkillSnapshotV1(StrictContract):
    contract_version: Literal['capability-skill-snapshot.v1'] = 'capability-skill-snapshot.v1'
    skill_id: str = Field(min_length=1, max_length=80)
    canonical_name: str = Field(min_length=1, max_length=150)
    category_code: str = Field(min_length=1, max_length=80)
    subcategory_code: str | None = Field(default=None, max_length=80)
    aliases: list[str] = Field(default_factory=list)
    status: Literal['active', 'inactive'] = 'active'


class StandardSkillSnapshotV2(StrictContract):
    contract_version: Literal['capability-skill-snapshot.v2'] = 'capability-skill-snapshot.v2'
    skill_id: str = Field(min_length=1, max_length=80)
    canonical_name: str = Field(min_length=1, max_length=150)
    aliases: list[str] = Field(default_factory=list)
    classifications: list[SkillClassificationV1]
    taxonomy_version: str = Field(min_length=1, max_length=128)
    status: Literal['active', 'inactive'] = 'active'


class StandardPositionRef(StrictContract):
    position_id: str
    canonical_name: str
    category_code: str | None = None


class PositionReference(StrictContract):
    position_id: str
    required_skills: list[dict] = Field(default_factory=list)
