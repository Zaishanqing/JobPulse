from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobgraph_contracts.offline_api_docs import install_offline_api_docs


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FASTAPI_APPLICATIONS = (
    "apps/api/app/main.py",
    "services/jd-extraction/src/main.py",
    "services/cv-extraction/api/main.py",
    "services/embedding-service/app/main.py",
    "services/matching-service/app/bootstrap/application.py",
    "services/knowledge-graph/app/bootstrap/application.py",
    "services/trend-intelligence/app/bootstrap.py",
    "services/emerging-discovery/app/bootstrap/application.py",
    "services/crawler/unified_api/main.py",
)


def _application() -> FastAPI:
    app = FastAPI(
        title="Offline documentation test",
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_swagger_and_redoc_html_only_reference_local_assets():
    client = TestClient(_application())

    swagger = client.get("/docs")
    assert swagger.status_code == 200
    assert "https://" not in swagger.text
    assert "http://" not in swagger.text
    assert '/_api-docs-assets/swagger-ui-bundle.js' in swagger.text
    assert '/_api-docs-assets/swagger-ui.css' in swagger.text
    assert '"validatorUrl": null' in swagger.text

    redoc = client.get("/redoc")
    assert redoc.status_code == 200
    assert "https://" not in redoc.text
    assert "http://" not in redoc.text
    assert '/_api-docs-assets/redoc.standalone.js' in redoc.text


def test_openapi_and_every_documentation_asset_are_served_locally():
    client = TestClient(_application())

    assert client.get("/openapi.json").status_code == 200
    for asset in (
        "favicon-32x32.png",
        "manifest.json",
        "redoc.standalone.js",
        "swagger-ui-bundle.js",
        "swagger-ui.css",
    ):
        response = client.get(f"/_api-docs-assets/{asset}")
        assert response.status_code == 200
        assert response.content


def test_installation_rejects_fastapi_default_cdn_routes():
    app = FastAPI()

    try:
        install_offline_api_docs(app)
    except RuntimeError as exc:
        assert "docs_url=None" in str(exc)
    else:
        raise AssertionError("default CDN-backed documentation routes must be rejected")


def test_every_production_fastapi_application_installs_offline_documentation():
    for relative_path in PRODUCTION_FASTAPI_APPLICATIONS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "docs_url=None" in source, relative_path
        assert "redoc_url=None" in source, relative_path
        assert "install_offline_api_docs(" in source, relative_path
