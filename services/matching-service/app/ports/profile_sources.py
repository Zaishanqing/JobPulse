"""Inbound cross-service profile ports.

Sources return explicit contract payloads.  They never expose another service's
database, ORM entity, or internal repository.
"""

from typing import Protocol


class CVProfileSource(Protocol):
    def fetch_cv_profile(self, cv_id: str) -> object: ...


class PositionProfileSource(Protocol):
    def fetch_position_profile(self, position_id: str) -> object: ...
    def fetch_enterprise_job_profile(self, position_id: str) -> object: ...
