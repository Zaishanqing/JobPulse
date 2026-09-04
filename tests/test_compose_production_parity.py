from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "infra" / "compose" / "docker-compose.candidate.yml"
PARITY_PATH = ROOT / "infra" / "compose" / "production-parity.yaml"
QUICKSTART_PATH = ROOT / "docs" / "deployment" / "quickstart.md"
DEPLOYMENT_PATH = ROOT / "docs" / "deployment" / "deployment.md"
COMPOSE_README_PATH = ROOT / "infra" / "compose" / "README.md"
RESET_SCRIPT_PATH = ROOT / "scripts" / "compose-reset.ps1"
READINESS_SCRIPT_PATH = ROOT / "scripts" / "compose-readiness.ps1"
STATUS_SCRIPT_PATH = ROOT / "scripts" / "compose-status.ps1"


def _documents() -> tuple[dict, dict]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    parity = yaml.safe_load(PARITY_PATH.read_text(encoding="utf-8"))
    return compose, parity


def test_compose_satisfies_declared_production_parity_denominator():
    compose, parity = _documents()
    services = compose["services"]

    for service_name in parity["default_services"]:
        assert service_name in services
        assert "profiles" not in services[service_name]

    for profile, service_names in parity["profiles"].items():
        for service_name in service_names:
            assert service_name in services
            assert profile in services[service_name]["profiles"]


def test_compose_profiles_and_parity_contract_cannot_drift():
    compose, parity = _documents()
    declared_profiles = set(parity["profiles"])
    compose_profiles = {
        profile
        for service in compose["services"].values()
        for profile in service.get("profiles", [])
    }
    assert compose_profiles == declared_profiles
    default_services = set(parity["default_services"])
    for service_name, service in compose["services"].items():
        profiles = set(service.get("profiles", []))
        assert not (service_name in default_services and profiles)
        for profile in profiles:
            assert service_name in parity["profiles"][profile]
    assert set(parity["profiles"]["model-extraction"]) == {
        "jd-extraction",
        "extraction-worker",
    }
    assert set(parity["profiles"]["crawler"]) == {
        "crawler-mysql",
        "crawler-api",
        "crawler-scheduler",
    }
    assert set(parity["profiles"]["full"]) == (
        set(parity["profiles"]["model-extraction"])
        | set(parity["profiles"]["cv-extraction"])
        | set(parity["profiles"]["semantic-demo"])
        | set(parity["profiles"]["crawler"])
    )


def test_every_declared_main_worker_has_an_independent_command_and_restart_policy():
    compose, parity = _documents()
    services = compose["services"]

    for service_name, command in parity["main_worker_commands"].items():
        service = services[service_name]
        assert service["command"] == [command]
        assert service["restart"] == "unless-stopped"
        assert service["environment"]["JD_EXTRACTION_WORKER_ENABLED"] is (
            service_name == "extraction-worker"
        )
        assert service["environment"]["CV_EXTRACTION_WORKER_ENABLED"] is (
            service_name == "cv-extraction-worker"
        )

    project = tomllib.loads((ROOT / "apps" / "api" / "pyproject.toml").read_text("utf-8"))
    scripts = project["project"]["scripts"]
    assert set(parity["main_worker_commands"].values()) <= set(scripts)


def test_async_dependencies_are_explicit_and_validation_is_enforced():
    compose, _ = _documents()
    services = compose["services"]
    matching_build_args = services["matching-api"]["build"]["args"]
    assert matching_build_args["INSTALL_RESPONSIBILITY_CE"] == (
        "${MATCHING_INSTALL_RESPONSIBILITY_CE:-false}"
    )
    assert services["matching-api"]["environment"]["MATCHING_RESPONSIBILITY_CE_MODE"] == (
        "${MATCHING_RESPONSIBILITY_CE_MODE:-disabled}"
    )
    assert services["matching-api"]["environment"]["MATCHING_RESPONSIBILITY_CE_MANIFEST_PATH"] == (
        "${MATCHING_RESPONSIBILITY_CE_MANIFEST_PATH:-/models/responsibility-ce-v1/manifest.json}"
    )
    assert services["matching-api"]["volumes"] == [
        "${MATCHING_RESPONSIBILITY_CE_MODEL_HOST_PATH:-../models/responsibility-ce-v1}:/models/responsibility-ce-v1:ro"
    ]
    for worker in (
        "extraction-worker",
        "validation-worker",
        "kg-outbox-worker",
        "knowledge-graph-worker",
        "matching-worker",
        "matching-dispatcher",
        "trend-intelligence-worker",
    ):
        assert services[worker]["healthcheck"]["test"][:2] == ["CMD", "python"]
        assert "os.kill(1, 0)" in services[worker]["healthcheck"]["test"][3]

    assert services["validation-worker"]["environment"]["DATA_VALIDATION_MODE"] == (
        "${DATA_VALIDATION_MODE:-enforce}"
    )
    assert services["knowledge-graph-backend"]["environment"]["KG_EMBEDDING_ENDPOINT"] == (
        "http://embedding-service:8000"
    )
    assert "KG_EMBEDDING_ENDPOINT" not in services["knowledge-graph-bootstrap"]["environment"]
    assert set(services["kg-outbox-worker"]["depends_on"]) >= {
        "main-backend",
        "knowledge-graph-backend",
        "matching-api",
    }
    assert set(services["cv-extraction-worker"]["depends_on"]) >= {
        "main-backend",
        "cv-extraction",
    }
    assert set(services["extraction-worker"]["depends_on"]) >= {
        "main-backend",
        "jd-extraction",
    }
    assert set(services["matching-vector-worker-semantic-demo"]["depends_on"]) >= {
        "main-backend",
        "embedding-service",
        "qdrant",
    }
    assert set(services["crawler-api"]["depends_on"]) == {"crawler-mysql"}
    assert services["crawler-api"]["environment"]["CRAWLER_EMBEDDED_SCHEDULER_ENABLED"] == "false"
    assert services["crawler-api"]["environment"]["OFFLINE_BUNDLE_DIR"] == "/app/output"
    assert services["crawler-scheduler"]["environment"]["OFFLINE_BUNDLE_DIR"] == "/app/output"
    assert services["main-backend"]["environment"]["ACQUISITION_BUNDLE_DIR"] == "/app/bundles"
    assert "../services/crawler/output:/app/bundles:ro" in services["main-backend"]["volumes"]
    assert services["main-backend"]["environment"]["CRAWLER_BASE_URL"] == (
        "${CRAWLER_BASE_URL:-http://crawler-api:8000}"
    )
    assert services["crawler-api"]["environment"]["INTERNAL_SERVICE_TOKEN"] == (
        "${CRAWLER_INTERNAL_TOKEN:-jobpulse-crawler-internal-token-0123456789abcdef}"
    )
    assert "CRAWLER_CORS_ALLOWED_ORIGINS" in services["crawler-api"]["environment"]["CORS_ALLOWED_ORIGINS"]
    assert set(services["crawler-scheduler"]["depends_on"]) >= {
        "crawler-mysql",
        "crawler-api",
    }


def test_first_party_images_accept_an_immutable_release_tag():
    compose, _ = _documents()
    for service in compose["services"].values():
        image = service.get("image", "")
        if image.startswith("jobpulse-"):
            assert "${JOBPULSE_IMAGE_TAG:-candidate}" in image


def test_kg_real_data_initialization_is_explicit_and_full_only_checks_readiness():
    compose, parity = _documents()
    services = compose["services"]

    assert parity["profiles"]["kg-init"] == [
        "knowledge-graph-real-data-init",
        "knowledge-graph-readiness-check",
    ]
    initializer = services["knowledge-graph-real-data-init"]
    checker = services["knowledge-graph-readiness-check"]
    assert initializer["profiles"] == ["kg-init"]
    assert checker["profiles"] == ["kg-init"]
    assert initializer["restart"] == "no"
    assert checker["restart"] == "no"
    assert initializer["command"][:2] == ["python", "scripts/build_kg_graphs.py"]
    assert checker["command"] == ["python", "scripts/check_kg_graph_readiness.py"]
    assert "knowledge-graph-real-data-init" not in parity["profiles"]["full"]
    assert "knowledge-graph-readiness-check" not in parity["profiles"]["full"]


def test_root_deployment_commands_pin_the_infra_project_directory():
    quickstart = QUICKSTART_PATH.read_text(encoding="utf-8")
    deployment = DEPLOYMENT_PATH.read_text(encoding="utf-8")
    compose_readme = COMPOSE_README_PATH.read_text(encoding="utf-8")

    assert "docker compose --env-file .\\infra\\.env -f" not in quickstart
    assert quickstart.count("docker compose --project-directory .\\infra") == 7
    assert "--project-directory .\\infra" in deployment
    assert "--project-directory ./infra" in deployment
    assert "docker compose --project-directory infra" in compose_readme


def test_reset_covers_inactive_profile_volumes_and_readiness_distinguishes_kg_data():
    reset_script = RESET_SCRIPT_PATH.read_text(encoding="utf-8")
    readiness_script = READINESS_SCRIPT_PATH.read_text(encoding="utf-8")
    status_script = STATUS_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '-AdditionalProfiles @("*")' in reset_script
    assert "config --format json" in reset_script
    assert 'label=com.docker.compose.project=$composeProjectName' in reset_script
    assert "docker volume rm @remainingVolumes" in reset_script

    assert "[switch]$RequirePublishedKg" in readiness_script
    assert '$publishedKgRequired = $Full -or $RequirePublishedKg' in readiness_script
    assert '$service -eq "knowledge-graph-backend"' in readiness_script
    assert '"health"' in readiness_script
    assert "-RequirePublishedKg:$RequirePublishedKg" in status_script
