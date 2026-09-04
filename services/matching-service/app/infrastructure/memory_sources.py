"""Test/development-only in-memory profile source adapters."""

from __future__ import annotations

from copy import deepcopy


class InMemoryCVProfileSource:
    def __init__(self, profiles: dict[str, object] | None = None) -> None:
        self._profiles = dict(profiles or {})

    def fetch_cv_profile(self, cv_id: str) -> object:
        if cv_id not in self._profiles:
            raise KeyError(cv_id)
        return deepcopy(self._profiles[cv_id])


class InMemoryPositionProfileSource:
    def __init__(
        self,
        profiles: dict[str, object] | None = None,
        enterprise_profiles: dict[str, object] | None = None,
    ) -> None:
        self._profiles = dict(profiles or {})
        self._enterprise_profiles = dict(enterprise_profiles or {})

    def fetch_position_profile(self, position_id: str) -> object:
        if position_id not in self._profiles:
            raise KeyError(position_id)
        return deepcopy(self._profiles[position_id])

    def fetch_enterprise_job_profile(self, position_id: str) -> object:
        profiles = self._enterprise_profiles or self._profiles
        if position_id not in profiles:
            raise KeyError(position_id)
        return deepcopy(profiles[position_id])
