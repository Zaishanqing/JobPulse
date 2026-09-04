"""Validate the authenticated competition business projections end to end."""

from __future__ import annotations

import json
import os
import urllib.request


BASE_URL = os.environ.get("COMPETITION_SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
USERNAME = os.environ.get("COMPETITION_SMOKE_USERNAME", "demo_admin")
PASSWORD = os.environ.get("COMPETITION_SMOKE_PASSWORD", "password123")
REQUIRE_ARCHIVED_COMPETITION_RESULTS = (
    os.environ.get("COMPETITION_SMOKE_REQUIRE_ARCHIVED_RESULTS", "false").lower()
    == "true"
)


def request(path: str, *, token: str | None = None, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def data_envelope(payload: dict, endpoint: str) -> object:
    if not isinstance(payload, dict) or payload.get("code") != 0 or "data" not in payload:
        raise RuntimeError(f"{endpoint} returned an invalid success envelope")
    return payload["data"]


def require_non_empty_list(value: object, endpoint: str) -> None:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"{endpoint} returned an empty or invalid list")


def main() -> int:
    login_endpoint = "/api/v1/auth/login"
    login = request(login_endpoint, method="POST", body={"username": USERNAME, "password": PASSWORD})
    login_data = data_envelope(login, login_endpoint)
    if not isinstance(login_data, dict) or not isinstance(login_data.get("access_token"), str):
        raise RuntimeError("login did not return an access token")
    if login_data.get("token_type") != "bearer":
        raise RuntimeError("login returned an unexpected token type")
    token = login_data["access_token"]

    me_endpoint = "/api/v1/auth/me"
    me = data_envelope(request(me_endpoint, token=token), me_endpoint)
    if not isinstance(me, dict) or me.get("username") != USERNAME or not me.get("is_active"):
        raise RuntimeError("authenticated /auth/me response does not identify the seeded account")

    for endpoint in ("/api/v1/positions", "/api/v1/skills"):
        require_non_empty_list(data_envelope(request(endpoint), endpoint), endpoint)

    if REQUIRE_ARCHIVED_COMPETITION_RESULTS:
        for section in ("a", "b", "c", "d"):
            endpoint = f"/api/v1/innovation/competition-results/{section}"
            payload = data_envelope(request(endpoint, token=token), endpoint)
            if not isinstance(payload, dict):
                raise RuntimeError(f"competition {section.upper()} returned a non-object payload")
            expected_schema = f"competition-results-{section}.v1"
            if payload.get("schema_version") != expected_schema:
                raise RuntimeError(f"competition {section.upper()} returned an unexpected schema version")
            if section in {"a", "b"} and not payload.get("sections"):
                raise RuntimeError(f"competition {section.upper()} has no sections")
            if section == "c" and payload.get("status") not in {"complete", "incomplete", "pending", "failed"}:
                raise RuntimeError(f"competition C has invalid status: {payload.get('status')}")
            if section == "c" and not isinstance(payload.get("matching"), dict):
                raise RuntimeError("competition C has no matching projection")
            if section == "d":
                if payload.get("status") not in {"partial_closed", "failed"}:
                    raise RuntimeError(f"competition D has invalid status: {payload.get('status')}")
                if not isinstance(payload.get("emerging"), dict) or not isinstance(payload.get("enterprise"), dict):
                    raise RuntimeError("competition D is missing emerging or enterprise projections")
                stale = (payload.get("enterprise") or {}).get("stale_transition")
                if stale and (stale.get("status") != "stale" or stale.get("rank_eligible") is not False):
                    raise RuntimeError("stale enterprise evaluation is incorrectly rank-eligible")
        print("competition business smoke passed: auth, catalogs, and archived A/B/C/D projections")
    else:
        print("competition business smoke passed: auth and catalogs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
