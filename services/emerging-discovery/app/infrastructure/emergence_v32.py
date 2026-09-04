"""HTTP adapter for the single formal EMERGE v3.2 policy owned by KG."""

from __future__ import annotations

from typing import Any

import httpx


class EmergenceV32GatewayError(RuntimeError):
    pass


class KnowledgeGraphEmergenceV32Client:
    def __init__(self, base_url: str, username: str, password: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def evaluate(
        self,
        *,
        dataset_id: str,
        clusters: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                login = client.post(
                    f"{self.base_url}/api/v1/auth/token",
                    json={"username": self.username, "password": self.password},
                )
                login.raise_for_status()
                token = login.json()["data"]["access_token"]
                response = client.post(
                    f"{self.base_url}/api/v1/integrations/emergence/v3.2/evaluate",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"dataset_id": dataset_id, "clusters": clusters},
                )
                response.raise_for_status()
                body = response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmergenceV32GatewayError("EMERGE v3.2 policy service is unavailable") from exc
        if body.get("algorithm") != "emerge_v3_2":
            raise EmergenceV32GatewayError("KG returned an unexpected emergence algorithm")
        results = body.get("clusters")
        if not isinstance(results, list):
            raise EmergenceV32GatewayError("KG emergence response is missing clusters")
        by_id = {
            str(item.get("cluster_id")): item
            for item in results
            if isinstance(item, dict) and item.get("cluster_id")
        }
        expected = {str(item["cluster_id"]) for item in clusters}
        if set(by_id) != expected:
            raise EmergenceV32GatewayError("KG emergence response does not match requested clusters")
        return by_id

