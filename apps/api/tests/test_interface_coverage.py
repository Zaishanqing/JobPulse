from app.main import app


def test_openapi_covers_required_interface_list():
    text = open("docs/interface-catalog.md", encoding="utf-8").read()
    methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in methods
    }
    required = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 6 or parts[1] not in methods or parts[4] not in {"P0", "P1", "P2"}:
            continue
        if parts[5].strip() in {"否", "接口保留"}:
            continue
        path = parts[2].strip("` ")
        full_path = path if path.startswith("/api/v1") else f"/api/v1{path}"
        required.append((parts[1], full_path))

    missing = sorted(set(required) - actual)

    assert missing == []


def test_sensitive_skill_and_password_operations_require_authentication():
    schema = app.openapi()["paths"]
    protected_operations = {
        ("put", "/api/v1/auth/password"),
        ("post", "/api/v1/skills/normalize"),
        ("post", "/api/v1/skills/normalize-batch"),
        ("get", "/api/v1/skills/normalize-candidates"),
        ("get", "/api/v1/skills/catalog/draft"),
        ("post", "/api/v1/skills/catalog/publish"),
        ("post", "/api/v1/skills/normalize-candidates/re-normalize"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/confirm"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/reject"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/map-existing"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/create-new"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/exclude-non-skill"),
        ("post", "/api/v1/skills/normalize-candidates/{candidate_id}/defer"),
    }

    missing_security = sorted(
        (method.upper(), path)
        for method, path in protected_operations
        if not schema[path][method].get("security")
    )

    assert missing_security == []


def test_every_non_public_operation_declares_authentication():
    public_operations = {
        ("get", "/health"),
        ("get", "/readiness"),
        ("get", "/api/v1/health"),
        ("get", "/api/v1/readiness"),
        ("get", "/api/v1/extraction-modes/readiness"),
        ("post", "/api/v1/auth/register"),
        ("post", "/api/v1/auth/login"),
        ("get", "/api/v1/skill-categories/tree"),
        ("get", "/api/v1/skills/domain-tree"),
        ("get", "/api/v1/skill-taxonomy/nodes"),
        ("get", "/api/v1/skills"),
        ("get", "/api/v1/skills/{skill_id}"),
        ("get", "/api/v1/skills/{skill_id}/aliases"),
        ("get", "/api/v1/skills/{skill_id}/classifications"),
        ("get", "/api/v1/skills/catalog/versions/latest"),
        ("get", "/api/v1/skills/catalog/versions/{catalog_version}"),
        ("get", "/api/v1/skills/catalog/downstream"),
        ("get", "/api/v1/positions"),
        ("get", "/api/v1/position-categories/tree"),
        ("get", "/api/v1/positions/{position_id}"),
        ("get", "/api/v1/skills/{skill_id}/evidence"),
        ("get", "/api/v1/positions/{position_id}/evidence"),
        ("get", "/api/v1/relations/{relation_id}/evidence"),
    }
    methods = {"get", "post", "put", "patch", "delete"}
    schema = app.openapi()["paths"]
    actual_public = {
        (method, path)
        for path, operations in schema.items()
        for method, operation in operations.items()
        if method in methods and not operation.get("security")
    }

    assert actual_public == public_operations
