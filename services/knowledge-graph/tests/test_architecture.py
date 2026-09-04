import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
APP = ROOT / "app"


def modules_under(relative: str):
    return (APP / relative).rglob("*.py")


def imports(path: Path):
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def calls(path: Path, method: str):
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
    ]


def test_domain_has_no_framework_or_persistence_dependencies():
    forbidden = ("fastapi", "sqlalchemy", "app.models", "app.infrastructure")
    violations = {
        str(path.relative_to(ROOT)): module
        for path in modules_under("domain")
        for module in imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}


def test_application_depends_on_ports_not_sqlalchemy_models():
    forbidden = ("fastapi", "sqlalchemy", "app.models", "app.infrastructure")
    violations = {
        str(path.relative_to(ROOT)): module
        for path in modules_under("application")
        for module in imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}


def test_api_does_not_import_orm_or_infrastructure():
    forbidden = ("sqlalchemy", "app.models", "app.infrastructure")
    violations = {
        str(path.relative_to(ROOT)): module
        for path in modules_under("api")
        for module in imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}


def test_api_and_main_do_not_own_transactions_or_orm_queries():
    paths = [APP / "main.py", *modules_under("api")]
    forbidden = ("commit", "rollback", "flush", "add", "delete", "query", "select")
    violations = {
        str(path.relative_to(ROOT)): method
        for path in paths
        for method in forbidden
        if calls(path, method)
    }
    assert violations == {}


def test_main_is_only_composition_entrypoint():
    tree = ast.parse((APP / "main.py").read_text("utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert functions == {"create_app"}


def test_repositories_do_not_commit_transactions():
    violations = {
        str(path.relative_to(ROOT)): len(calls(path, "commit"))
        for path in (APP / "infrastructure" / "sqlalchemy").glob("*repositories.py")
        if calls(path, "commit")
    }
    assert violations == {}


def test_structured_extraction_is_the_only_business_read_model():
    repositories = (
        APP / "infrastructure" / "sqlalchemy" / "repository_adapters.py"
    ).read_text("utf-8")
    queries = "\n".join(
        path.read_text("utf-8")
        for path in (APP / "infrastructure" / "sqlalchemy").glob("query_*.py")
    )
    workflow = (
        APP / "infrastructure" / "sqlalchemy" / "graph_persistence.py"
    ).read_text("utf-8")
    assert "record.payload" not in repositories
    assert "JDExtractionRecord" not in queries
    assert "ex.payload" not in workflow
    assert "load_structured_extraction" in repositories
    assert "load_structured_extraction" in queries


def test_repository_ports_expose_persistence_operations_only():
    source = (APP / "ports" / "repositories.py").read_text("utf-8")
    forbidden_workflows = (
        "extract", "normalize", "build", "publish", "rollback", "assess"
    )
    assert not any(f"def {name}" in source for name in forbidden_workflows)
    assert not (APP / "ports" / "workflows.py").exists()


def test_legacy_graph_workflow_and_workflow_adapters_are_absent():
    sqlalchemy = APP / "infrastructure" / "sqlalchemy"
    assert not (sqlalchemy / "graph_workflow.py").exists()
    assert not (sqlalchemy / "workflow_adapter.py").exists()
    forbidden = {"build", "publish", "rollback", "normalize", "assess", "extract_default"}
    violations = []
    for path in sqlalchemy.glob("*.py"):
        if path.name == "unit_of_work.py":
            continue
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if method.name in forbidden:
                        violations.append(f"{path.name}:{node.name}.{method.name}")
    assert violations == []


def test_application_owns_graph_write_orchestration():
    source = (APP / "application" / "use_cases.py").read_text("utf-8")
    for call in (
        "uow.graph_builds.load_facts",
        "build_graph_plan",
        "uow.graph_builds.save_plan",
        "uow.graph_versions.load_publish_facts",
        "evaluate_publish_gate",
        "uow.graph_versions.save_published",
        "uow.graph_versions.load_rollback_facts",
        "uow.graph_versions.save_rollback",
    ):
        assert call in source


def test_core_public_signatures_do_not_use_bare_dict_or_any():
    violations = []
    for path in [*modules_under("application"), *modules_under("domain")]:
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            annotations = [node.returns]
            annotations.extend(argument.annotation for argument in node.args.args)
            for annotation in annotations:
                if annotation is None:
                    continue
                value = ast.unparse(annotation)
                if value == "dict" or value.startswith("dict[") or "Any" in value:
                    violations.append(f"{path.relative_to(ROOT)}:{node.name}:{value}")
    assert violations == []


def test_core_commands_do_not_hide_domain_values_in_serialized_payloads():
    source = (APP / "application" / "contracts.py").read_text("utf-8")
    assert "class StructuredFactResult" not in source
    assert "values: SerializedPayload" not in source
    assert "payload: SerializedPayload" not in source
    assert "summary: SerializedPayload" not in source
    for name in (
        "JDDocumentInput",
        "PublishedJDFact",
        "ExtractionResult",
        "NormalizationResult",
        "BuildSummary",
    ):
        assert name in source


def test_core_does_not_identify_infrastructure_errors_by_name():
    sources = "\n".join(
        path.read_text("utf-8")
        for layer in ("application", "domain", "ports")
        for path in modules_under(layer)
    )
    assert "__class__.__name__" not in sources
    assert "__import__(" not in sources


def test_core_graph_writes_use_explicit_commands_and_results():
    contracts = (APP / "application" / "contracts.py").read_text("utf-8")
    for name in (
        "DocumentWorkflowCommand",
        "ExtractionResult",
        "NormalizationResult",
        "BuildGraphCommand",
        "BuildGraphResult",
        "PublishGraphCommand",
        "RollbackGraphCommand",
        "GraphVersionResult",
    ):
        assert f"class {name}" in contracts


def test_graph_persistence_has_no_legacy_normalizer_or_persistence_copies():
    path = APP / "infrastructure" / "sqlalchemy" / "graph_persistence.py"
    tree = ast.parse(path.read_text("utf-8"))
    definitions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not {
        "Normalizer", "persist_extracted", "persist_normalized", "latest_record",
        "align_quote", "align_extraction", "normalize_salary",
    } & definitions


def test_no_wildcard_imports_in_application_or_scripts():
    violations = []
    for path in [*APP.rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_ports_do_not_depend_on_infrastructure():
    violations = {
        str(path.relative_to(ROOT)): module
        for path in modules_under("ports")
        for module in imports(path)
        if module.startswith("app.infrastructure")
    }
    assert violations == {}


def test_frontend_layering_and_page_api_boundaries():
    frontend = ROOT.parents[1] / "apps" / "web" / "src"
    shared_violations = {
        str(path.relative_to(ROOT.parents[1]))
        for path in (frontend / "shared").rglob("*.ts*")
        if "features/" in path.read_text("utf-8").replace("\\", "/")
    }
    shell = (frontend / "app" / "ApplicationShell.tsx").read_text("utf-8")
    entrypoint = (frontend / "App.tsx").read_text("utf-8")
    assert shared_violations == set()
    assert "api(" not in shell and "api<" not in shell
    assert "useState" not in entrypoint and "fetch(" not in entrypoint


def test_new_code_does_not_depend_on_legacy_services():
    protected = [*modules_under("api"), *modules_under("application"),
                 *modules_under("domain"), *modules_under("ports"),
                 *modules_under("infrastructure")]
    violations = {
        str(path.relative_to(ROOT))
        for path in protected
        if any(module.startswith("app.services") for module in imports(path))
    }
    assert violations == set()


def test_query_service_is_composed_from_single_responsibility_mixins():
    facade = APP / "infrastructure" / "sqlalchemy" / "query_service.py"
    assert len(facade.read_text("utf-8").splitlines()) < 60
    names = {path.name for path in facade.parent.glob("query_*.py")}
    assert {
        "query_documents.py",
        "query_catalog.py",
        "query_builds.py",
        "query_graphs.py",
        "query_evidence.py",
        "query_reviews.py",
        "query_versions.py",
            "query_profiles.py",
    } <= names


def test_p0_repository_adapter_cannot_regain_complete_business_workflows():
    path = APP / "infrastructure" / "sqlalchemy" / "repository_adapters.py"
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    methods = {
        node.name
        for class_node in tree.body
        if isinstance(class_node, ast.ClassDef)
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {
        "import_fact", "resolve", "open_draft", "modify_relation", "claim",
        "complete", "_hash", "_version", "_compare_versions", "_persist_fact",
        "_authoritative_document",
    } & methods


def test_only_audit_adapter_calls_audit_service_and_review_registry_is_hidden():
    path = APP / "infrastructure" / "sqlalchemy" / "repository_adapters.py"
    source = path.read_text("utf-8")
    tree = ast.parse(source, filename=str(path))
    direct_calls = []
    for class_node in tree.body:
        if not isinstance(class_node, ast.ClassDef):
            continue
        for node in ast.walk(class_node):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "AuditService"
                and node.func.attr == "record"
            ):
                direct_calls.append(class_node.name)
    assert direct_calls == ["SqlAlchemyAuditRepository"]
    assert "build_review_handler_registry" not in source
    assert "handler_for(" not in source


def test_p0_use_cases_show_facts_decision_plan_and_application_side_effects():
    source = (APP / "application" / "use_cases.py").read_text("utf-8")
    required_calls = {
        "ImportPublishedJDFactUseCase": (
            "load_validation_facts", "decide_published_fact_import",
            "save_import_plan", "self._audit",
        ),
        "ResolveUnresolvedSkillUseCase": (
            "load_skill_resolution_facts", "decide_skill_resolution",
            "apply_skill_resolution_plan", "append_review_event",
        ),
        "OpenGraphDraftUseCase": (
            "load_graph_draft_facts", "decide_graph_draft",
            "save_graph_draft_plan",
        ),
        "ModifyRelationUseCase": (
            "load_relation_edit_facts", "decide_relation_edit",
            "apply_relation_edit_plan", "_apply_review_task_dedup",
        ),
    }
    tree = ast.parse(source)
    classes = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    for class_name, fragments in required_calls.items():
        assert all(fragment in classes[class_name] for fragment in fragments)
    # Claim and complete intentionally share one typed orchestration function.
    for fragment in (
        "load_review_task_facts", "decide_review_task_transition",
        "apply_review_task_plan", "review_effects.apply",
        "append_review_event", "uow.audits.record",
    ):
        assert fragment in source


def test_infrastructure_does_not_choose_p0_business_transitions():
    source = (
        APP / "infrastructure" / "sqlalchemy" / "repository_adapters.py"
    ).read_text("utf-8")
    assert "command.action" not in source
    assert "completion.action" not in source
    assert '"approve": "approved"' not in source
    assert '"reject": "rejected"' not in source
    assert '"modify": "modified"' not in source


def test_review_dedup_and_build_review_workflow_cannot_return_to_infrastructure():
    infrastructure = APP / "infrastructure" / "sqlalchemy"
    forbidden_methods = {"ensure_review_task", "_append_review"}
    literal_status_filters = []
    reason_merges = []
    review_constructors = []
    for path in infrastructure.glob("*.py"):
        source = path.read_text("utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in forbidden_methods
            if (
                path.name == "graph_build_repository.py"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ReviewTask"
            ):
                review_constructors.append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "in_"
                and node.args
                and isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set))
                and any(
                    isinstance(item, ast.Constant)
                    and item.value in {"pending", "claimed", "modified"}
                    for item in node.args[0].elts
                )
            ):
                literal_status_filters.append((path.name, node.lineno))
            segment = ast.get_source_segment(source, node) or ""
            if "reasons" in segment and (
                isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                or isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "sorted"
            ):
                reason_merges.append((path.name, node.lineno))
    assert review_constructors == []
    assert literal_status_filters == []
    assert reason_merges == []

    use_cases = (APP / "application" / "use_cases.py").read_text("utf-8")
    build_class = next(
        node
        for node in ast.parse(use_cases).body
        if isinstance(node, ast.ClassDef) and node.name == "BuildGraphUseCase"
    )
    build_source = ast.get_source_segment(use_cases, build_class) or ""
    assert "save_plan(plan)" in build_source
    assert "_apply_review_task_dedup" in build_source

    persistence = (
        APP / "infrastructure" / "sqlalchemy" / "graph_persistence.py"
    ).read_text("utf-8")
    publishing = (APP / "domain" / "publishing.py").read_text("utf-8")
    assert "OPEN_REVIEW_STATUSES" not in persistence
    assert "is_open_review_status" in publishing


def test_query_publish_gate_delegates_to_the_single_gate_pipeline():
    query_path = APP / "infrastructure" / "sqlalchemy" / "query_builds.py"
    query_source = query_path.read_text("utf-8")
    query_tree = ast.parse(query_source, filename=str(query_path))
    publish_gate = next(
        node
        for node in ast.walk(query_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "publish_gate"
    )
    called_names = {
        node.func.id
        for node in ast.walk(publish_gate)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names == {"publish_gate_status"}
    assert "from app.domain" not in query_source
    for forbidden in (
        "ReviewTask",
        "PositionSkillRelationDraft",
        "UnresolvedNormalizationItem",
        "ExtractionEvidence",
        "OPEN_REVIEW_TASK_STATUSES",
        "minimum_valid_samples",
        "non_empty_graph",
        "open_review_tasks",
        "relation_approval",
        "GateViolation",
    ):
        assert forbidden not in query_source

    persistence = (
        APP / "infrastructure" / "sqlalchemy" / "graph_persistence.py"
    ).read_text("utf-8")
    assert "load_publish_gate_facts(db, run)" in persistence
    assert (
        "publish_gate_result(evaluate_publish_gate(load_publish_gate_facts(db, run)))"
        in persistence
    )

    router = (APP / "api" / "router.py").read_text("utf-8")
    assert "publish_gate_errors(exc.errors)" in router
    assert "violation.support_id" not in router
    assert "violation.relation_id" not in router
    assert "violation.task_ids" not in router


def test_repository_hygiene_rejects_accidental_command_output_files():
    repository_root = ROOT.parents[2]
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository_root.as_posix()}",
            "-C",
            str(repository_root),
            "ls-files",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    tracked = tuple(
        name.decode("utf-8")
        for name in result.stdout.split(b"\0")
        if name
    )
    forbidden_name = "olve clean architecture P0 issues\uf022"
    assert forbidden_name not in tracked
    assert not (repository_root / forbidden_name).exists()
    assert all(
        not any(
            ord(character) < 32
            or 0xE000 <= ord(character) <= 0xF8FF
            for character in name
        )
        for name in tracked
    )
