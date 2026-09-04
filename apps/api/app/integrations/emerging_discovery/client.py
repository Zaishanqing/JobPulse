from __future__ import annotations

from typing import Any, NoReturn

import httpx

from app.core.config import settings
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError

CANDIDATE_STATUSES = frozenset(
    {
        "weak_signal",
        "incubating",
        "emerging_candidate",
        "stable_emerging_role",
        "official_position",
        "dead",
        "noise",
    }
)


class EmergingDiscoveryClient:
    """Stable HTTP-only boundary; this client never receives a discovery DB session."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.EMERGING_DISCOVERY_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.EMERGING_DISCOVERY_TIMEOUT_SECONDS
        self.token = token or settings.EMERGING_DISCOVERY_INTERNAL_TOKEN

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/v1/discovery-runs",
            json=payload,
            context="Emerging discovery run failed",
        )
        data = self._parse_envelope(response, context="Emerging discovery run failed")
        return self._validate_run_contract(data)

    def recompute_conclusion(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/v1/discovery-conclusion-recomputations",
            json=payload,
            context="Emerging conclusion recomputation failed",
        )
        data = self._parse_envelope(
            response, context="Emerging conclusion recomputation failed"
        )
        required = {
            "contract_version",
            "dataset_id",
            "release_id",
            "subject_ref",
            "algorithm_version",
            "config_hash",
            "request_fingerprint",
            "score",
            "business_state",
            "rank",
            "threshold_passed",
            "target_found",
        }
        missing = sorted(required - set(data))
        if (
            missing
            or data.get("contract_version") != "emerging-conclusion-recompute.v1"
            or not isinstance(data.get("threshold_passed"), bool)
            or not isinstance(data.get("target_found"), bool)
            or isinstance(data.get("score"), bool)
            or not isinstance(data.get("score"), (int, float))
        ):
            self._raise_contract_error(
                "Emerging conclusion recomputation returned a damaged contract",
                {"contract": "emerging-conclusion-recompute.v1", "missing_fields": missing},
            )
        return data

    def get_run_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET",
            f"/api/v1/discovery-runs/by-request-id/{request_id}",
            context="Emerging discovery run failed",
        )
        if response.status_code == 404:
            return None
        data = self._parse_envelope(response, context="Emerging discovery run failed")
        return self._validate_run_contract(data)

    def list_candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if candidate_id:
            params["candidate_id"] = candidate_id
        if window_id:
            params["window_id"] = window_id
        response = self._request(
            "GET",
            "/api/v1/candidates",
            params=params or None,
            context="Emerging discovery candidates failed",
        )
        data = self._parse_envelope(
            response, context="Emerging discovery candidates failed"
        )
        if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an invalid candidate list contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
                details={"contract": "candidates.v1"},
            )
        for item in data["candidates"]:
            self._validate_candidate_data(item, context="candidates.v1")
        return data

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/v1/candidates/{candidate_id}",
            context="Emerging discovery candidate failed",
        )
        data = self._parse_envelope(
            response,
            context="Emerging discovery candidate failed",
            not_found_message="Discovery candidate not found",
        )
        if not isinstance(data, dict) or not isinstance(data.get("candidate"), dict):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an invalid candidate detail contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
                details={"contract": "candidate-detail.v1"},
            )
        candidate = self._validate_candidate_data(
            data["candidate"], context="candidate-detail.v1"
        )
        if candidate.get("candidate_id") != candidate_id:
            self._raise_contract_error(
                "candidate detail candidate_id does not match the requested path",
                {"contract": "candidate-detail.v1", "path_candidate_id": candidate_id},
            )
        latest = data.get("latest_observation")
        if latest is not None:
            observation = self._validate_observation_data(
                latest, context="candidate-detail.v1"
            )
            if observation.get("candidate_id") != candidate_id:
                self._raise_contract_error(
                    "latest_observation candidate_id does not match the candidate",
                    {"contract": "candidate-detail.v1", "path_candidate_id": candidate_id},
                )
        return data

    def get_candidate_trajectory(self, candidate_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/v1/candidates/{candidate_id}/trajectory",
            context="Emerging discovery candidate trajectory failed",
        )
        data = self._parse_envelope(
            response,
            context="Emerging discovery candidate trajectory failed",
            not_found_message="Discovery candidate not found",
        )
        if not isinstance(data, dict) or not isinstance(data.get("trajectory"), list):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an invalid candidate trajectory contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
                details={"contract": "candidate-trajectory.v1"},
            )
        if data.get("candidate_id") != candidate_id:
            self._raise_contract_error(
                "trajectory candidate_id does not match the requested path",
                {"contract": "candidate-trajectory.v1", "path_candidate_id": candidate_id},
            )
        for item in data["trajectory"]:
            observation = self._validate_observation_data(
                item, context="candidate-trajectory.v1"
            )
            if observation.get("candidate_id") != candidate_id:
                self._raise_contract_error(
                    "trajectory observation candidate_id does not match the trajectory",
                    {"contract": "candidate-trajectory.v1", "path_candidate_id": candidate_id},
                )
        return data

    def get_candidate_diffusion(self, candidate_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/v1/candidates/{candidate_id}/diffusion-graph",
            context="Emerging discovery candidate diffusion failed",
        )
        data = self._parse_envelope(
            response,
            context="Emerging discovery candidate diffusion failed",
            not_found_message="Discovery candidate not found",
        )
        errors = []
        if data.get("schema_version") != "candidate-diffusion-graph.v1":
            errors.append("schema_version: candidate-diffusion-graph.v1 required")
        if data.get("candidate_id") != candidate_id:
            errors.append("candidate_id: must match requested path")
        if data.get("readonly") is not True:
            errors.append("readonly: true required")
        if not isinstance(data.get("nodes"), list):
            errors.append("nodes: list required")
        if not isinstance(data.get("edges"), list):
            errors.append("edges: list required")
        if errors:
            self._raise_contract_error(
                "invalid candidate-diffusion-graph.v1 contract",
                {"contract": "candidate-diffusion-graph.v1", "errors": errors},
            )
        return data

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        context: str,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            if method == "POST":
                return httpx.post(url, json=json, timeout=self.timeout, headers=headers)
            if method == "GET":
                return httpx.get(url, params=params, timeout=self.timeout, headers=headers)
            raise ValueError(f"unsupported HTTP method {method}")
        except httpx.RequestError as exc:
            raise EmergingDiscoveryError(
                "Emerging discovery service is unavailable",
                details={"reason": type(exc).__name__},
            ) from exc

    @staticmethod
    def _parse_envelope(
        response,
        *,
        context: str,
        not_found_message: str | None = None,
    ) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                upstream = response.json()
            except ValueError:
                upstream = {"body": response.text[:500]}
            error_code = "emerging_discovery_upstream_error"
            if response.status_code == 404 and not_found_message is not None:
                error_code = "emerging_discovery_not_found"
            raise EmergingDiscoveryError(
                not_found_message if response.status_code == 404 and not_found_message is not None else context,
                status_code=response.status_code,
                error_code=error_code,
                details={
                    "upstream_status": response.status_code,
                    "upstream": upstream,
                },
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise EmergingDiscoveryError(
                "Emerging discovery returned invalid JSON",
                status_code=502,
                error_code="emerging_discovery_invalid_json",
            ) from exc
        if not isinstance(body, dict):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an invalid contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
            )
        if body.get("code") != 0 or not isinstance(body.get("data"), dict):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an invalid contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
            )
        return body["data"]

    @staticmethod
    def _raise_contract_error(message: str, details: dict[str, Any]) -> NoReturn:
        raise EmergingDiscoveryError(
            message,
            status_code=502,
            error_code="emerging_discovery_contract_error",
            details=details,
        )

    @staticmethod
    def _validate_candidate_data(raw: Any, *, context: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: candidate entry is not an object",
                {"contract": context, "errors": ["candidate entry must be an object"]},
            )
        errors: list[str] = []
        for field in ("candidate_id", "first_seen_window_id", "last_seen_window_id"):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field}: non-empty string required")
        status = raw.get("status")
        if status not in CANDIDATE_STATUSES:
            errors.append(f"status: unknown lifecycle status {status!r}")
        age = raw.get("age")
        if not isinstance(age, int) or isinstance(age, bool) or age < 0:
            errors.append("age: non-negative integer required")
        current_cluster_id = raw.get("current_cluster_id")
        if current_cluster_id is not None and (
            not isinstance(current_cluster_id, str) or not current_cluster_id.strip()
        ):
            errors.append("current_cluster_id: string or null required")
        if not isinstance(raw.get("previous_cluster_ids"), list):
            errors.append("previous_cluster_ids: list required")
        if not isinstance(raw.get("identity_profile"), dict):
            errors.append("identity_profile: object required")
        for field in ("canonical_title", "display_title"):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field}: non-empty string required")
        if not isinstance(raw.get("definition"), dict):
            errors.append("definition: object required")
        for field in ("support_count", "company_coverage", "identity_stability"):
            value = raw.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{field}: integer required")
        for field in ("identity_similarity", "novelty_score", "emergence_score"):
            value = raw.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{field}: numeric required")
        for field in ("skill_similarity", "responsibility_similarity", "title_similarity", "membership_overlap"):
            value = raw.get(field)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                errors.append(f"{field}: numeric or null required")
        if not isinstance(raw.get("evidence"), dict):
            errors.append("evidence: object required")
        if errors:
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: candidate contract damaged",
                {"contract": context, "errors": errors},
            )
        return raw

    @staticmethod
    def _validate_match_evidence(raw: Any, *, context: str) -> None:
        if not isinstance(raw, dict):
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: match_evidence is not an object",
                {"contract": context, "errors": ["match_evidence must be an object"]},
            )
        if not raw:
            return
        errors: list[str] = []
        matched = raw.get("matched")
        if not isinstance(matched, bool):
            errors.append("match_evidence.matched: boolean required")
        closest = raw.get("closest_candidate_id")
        if closest is not None and not isinstance(closest, str):
            errors.append("match_evidence.closest_candidate_id: string or null required")
        for field in ("identity_similarity", "threshold"):
            value = raw.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"match_evidence.{field}: numeric required")
        components = raw.get("components")
        if not isinstance(components, dict):
            errors.append("match_evidence.components: object required")
        else:
            for field in (
                "title_similarity",
                "skill_similarity",
                "responsibility_similarity",
                "membership_overlap",
                "semantic_similarity",
            ):
                value = components.get(field)
                if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                    errors.append(f"match_evidence.components.{field}: numeric or null required")
        for field in ("decision_reason", "decision_version"):
            value = raw.get(field)
            if not isinstance(value, str):
                errors.append(f"match_evidence.{field}: string required")
        if errors:
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: match_evidence contract damaged",
                {"contract": context, "errors": errors},
            )

    @staticmethod
    def _validate_observation_data(raw: Any, *, context: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: observation entry is not an object",
                {"contract": context, "errors": ["observation entry must be an object"]},
            )
        errors: list[str] = []
        for field in (
            "observation_id",
            "candidate_id",
            "run_id",
            "cluster_id",
            "window_id",
            "title",
        ):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field}: non-empty string required")
        status = raw.get("status")
        if status not in CANDIDATE_STATUSES:
            errors.append(f"status: unknown lifecycle status {status!r}")
        for field in ("support_count", "company_count"):
            value = raw.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{field}: integer required")
        for field in ("emergence_score", "identity_similarity"):
            value = raw.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{field}: numeric required")
        for field in ("evidence", "match_evidence"):
            if not isinstance(raw.get(field), dict):
                errors.append(f"{field}: object required")
        if isinstance(raw.get("match_evidence"), dict):
            EmergingDiscoveryClient._validate_match_evidence(
                raw["match_evidence"], context=context
            )
        for field in ("skill_similarity", "responsibility_similarity", "title_similarity", "membership_overlap", "semantic_similarity"):
            value = raw.get(field)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                errors.append(f"{field}: numeric or null required")
        cluster_name = raw.get("cluster_name")
        if cluster_name is not None and not isinstance(cluster_name, str):
            errors.append("cluster_name: string or null required")
        created_at = raw.get("created_at")
        if created_at is not None and not isinstance(created_at, str):
            errors.append("created_at: string or null required")
        if errors:
            EmergingDiscoveryClient._raise_contract_error(
                f"invalid {context}: observation contract damaged",
                {"contract": context, "errors": errors},
            )
        return raw

    @staticmethod
    def _validate_run_contract(data: dict[str, Any]) -> dict[str, Any]:
        required = {
            "contract_version",
            "run_id",
            "request_id",
            "status",
            "run_id",
            "algorithm_version",
            "clusters",
        }
        missing = sorted(required - data.keys())
        if (
            missing
            or data.get("contract_version") != "discovery.v2"
            or data.get("status") != "succeeded"
            or not isinstance(data.get("clusters"), list)
        ):
            raise EmergingDiscoveryError(
                "Emerging discovery returned an incomplete run contract",
                status_code=502,
                error_code="emerging_discovery_contract_error",
                details={
                    "missing_fields": missing,
                    "contract_version": data.get("contract_version"),
                },
            )
        return data
