"""Build the JobPulse documentation inventory.

The inventory is deliberately generated from Git's tracked file list so caches,
dependencies, build output, and unrelated untracked notes cannot silently enter
documentation governance. Classification is conservative: a verification
pointer identifies where a claim should be checked; it does not certify every
statement in the document.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "docs" / "document-map.md"
DOCUMENT_SUFFIXES = {".md", ".mdx", ".rst", ".tex", ".docx", ".pdf"}
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".pnpm-store",
    ".test-artifacts",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
}

# These are reviewed migration candidates from the reorganization task. Keeping
# them explicit makes the generated inventory useful during and after moves.
MOVE_TARGETS = {
    "docs/integration/BASELINE.md": "docs/archive/integration-baseline-20260723/baseline.md",
    "docs/integration/OFFLINE_JD_BUNDLES.md": "docs/offline-jd-bundles.md",
    "docs/integration/PATH_OWNERSHIP.md": "docs/path-ownership.md",
    "docs/integration/ROOT_COMPOSE.md": "docs/deployment.md",
    "框架实现/docs/architecture/system-layer-design.md": "docs/system-layer-design.md",
    "框架实现/docs/architecture/system-layer-diagram.md": "docs/system-layer-diagram.md",
    "框架实现/docs/architecture/unified-site-integration.md": "docs/unified-site.md",
    "框架实现/docs/governance/documentation-standard.md": "docs/documentation-standard.md",
    "框架实现/docs/integrations/knowledge-graph/contract.md": "docs/knowledge-graph-contract.md",
    "框架实现/docs/integrations/knowledge-graph/operations.md": "docs/knowledge-graph-operations.md",
    "框架实现/docs/operations/risks.md": "docs/risks.md",
    "框架实现/docs/operations/status.md": "docs/status.md",
    "框架实现/docs/task-03-kg-compatibility.md": (
        "框架实现/docs/archive/kg-compatibility-20260720/task-03-kg-compatibility.md"
    ),
    "框架实现/docs/governance/p1-context-map.md": (
        "框架实现/docs/archive/clean-architecture-p1-20260720/p1-context-map.md"
    ),
    "框架实现/docs/governance/p1-public-contract-inventory.md": (
        "框架实现/docs/archive/clean-architecture-p1-20260720/"
        "p1-public-contract-inventory.md"
    ),
    "框架实现/docs/governance/p1-remediation-report.md": (
        "框架实现/docs/archive/clean-architecture-p1-20260720/"
        "p1-remediation-report.md"
    ),
    "框架实现/docs/governance/p1-side-effect-matrix.md": (
        "框架实现/docs/archive/clean-architecture-p1-20260720/"
        "p1-side-effect-matrix.md"
    ),
    "框架实现/docs/archive/task-02-remediation-2-report.md": (
        "框架实现/docs/archive/task-02-remediation-20260720/"
        "task-02-remediation-2-report.md"
    ),
    "框架实现/docs/guides/frontend-design-methodology.md": (
        "框架实现/docs/archive/frontend-design-methodology-20260720/"
        "frontend-design-methodology.md"
    ),
    "Extraction/jdextraction/JD 标注规范 V2.md": (
        "Extraction/jdextraction/docs/annotation-standard.md"
    ),
    "Extraction/jdextraction/JD 标注规范.md": (
        "Extraction/jdextraction/docs/archive/annotation-standard-v1/"
        "annotation-standard.md"
    ),
    "Extraction/cvextraction/CV 标注规范.md": (
        "Extraction/cvextraction/docs/annotation-standard.md"
    ),
    "Extraction/jdextraction/docs/normalization_taxonomy_audit.md": (
        "Extraction/jdextraction/docs/archive/normalization-taxonomy-20260718/audit.md"
    ),
    "Extraction/jdextraction/docs/constraint_layers.md": (
        "Extraction/jdextraction/docs/constraint-layers.md"
    ),
    "Extraction/jdextraction/docs/data_flow.md": (
        "Extraction/jdextraction/docs/data-flow.md"
    ),
    "Extraction/jdextraction/docs/field_hierarchy.md": (
        "Extraction/jdextraction/docs/field-hierarchy.md"
    ),
    "Extraction/jdextraction/docs/issues_and_solutions.md": (
        "Extraction/jdextraction/docs/issues-and-solutions.md"
    ),
    "Extraction/jdextraction/docs/review_rules.md": (
        "Extraction/jdextraction/docs/review-rules.md"
    ),
    "Extraction/jdextraction/docs/run_dataset_commands.md": (
        "Extraction/jdextraction/docs/run-dataset-commands.md"
    ),
    "trend_analysis/docs/superpowers/plans/2026-07-21-multi-source-signals-plan.md": (
        "trend_analysis/docs/archive/multi-source-signals-20260721/plan.md"
    ),
    "trend_analysis/docs/superpowers/specs/2026-07-21-multi-source-signals-design.md": (
        "trend_analysis/docs/archive/multi-source-signals-20260721/design.md"
    ),
}


def current_documents() -> list[str]:
    """Return the final governed document set relative to JobPulse.

    The scan includes newly created files before staging, while explicitly
    excluding dependencies, caches, build output, and the pre-existing untracked
    user note that is outside this task.
    """

    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in DOCUMENT_SUFFIXES
        and not any(part in SKIP_PARTS for part in path.parts)
        and path.name != "本批JD数据质量问题汇总.md"
    )


def classify(path: str) -> tuple[str, str, str]:
    """Return document type, scope, and conservative current status."""

    lower = path.lower()
    if "/archive/" in lower or Path(path).name.lower().startswith("archive-"):
        return "归档", "模块级或目录级", "archived"
    if path.startswith("docs/"):
        if Path(path).name == "document-map.md":
            return "治理清单", "仓库级", "active"
        if "/architecture/" in lower:
            status = "draft" if "system-layer-" in lower else "active"
            return "架构", "仓库级", status
        if "/api/" in lower or "/integrations/" in lower:
            return "契约", "仓库级", "active"
        if "/operations/" in lower:
            return "运维", "仓库级", "active"
        if "/governance/" in lower:
            return "治理", "仓库级", "active"
        return "仓库文档", "仓库级", "active"
    if lower.endswith(".tex") or lower.endswith(".pdf"):
        return "交付物", "交付物", "archived"
    if "/docs/superpowers/plans/" in lower:
        return "阶段计划", "模块级", "archived"
    if "/docs/superpowers/specs/" in lower:
        return "设计规格", "模块级", "draft"
    if "remediation" in lower or "audit" in lower or "task-" in lower:
        return "整改或任务记录", "模块级", "draft"
    if lower.endswith("readme.md"):
        return "README", "目录级", "active"
    if "/api/" in lower:
        return "API", "模块级", "active"
    if "/architecture/" in lower:
        status = "draft" if "system-layer-" in lower else "active"
        return "架构", "模块级", status
    if "/operations/" in lower:
        return "运维", "模块级", "active"
    if "/quality/" in lower:
        return "质量", "模块级", "active"
    if "/guides/" in lower:
        return "指南", "模块级", "active"
    if "/governance/" in lower:
        return "治理", "模块级", "active"
    if "标注规范" in path:
        return "标注规范", "模块级", "active"
    return "专题文档", "模块级", "draft"


def evidence(path: str, doc_type: str) -> str:
    """Return the evidence location used to verify the document's claims."""

    if doc_type == "交付物":
        return "交付物源文件；不声明当前运行事实"
    if doc_type in {"归档", "阶段计划"}:
        return "历史材料本身；不作为当前事实源"
    if path.startswith("services/knowledge-graph/"):
        return "服务代码、迁移、契约测试与 ADR"
    if path.startswith("services/emerging-discovery/"):
        return "服务代码、配置与测试"
    if path.startswith("services/matching-service/"):
        return "服务代码、配置、迁移与测试"
    if path.startswith("apps/api/"):
        return "主后端代码、迁移、配置与测试"
    if path.startswith("services/jd-extraction/"):
        return "JD Extraction 代码、配置与测试"
    if path.startswith("services/cv-extraction/"):
        return "CV Extraction 代码、配置与测试"
    if path.startswith("services/crawler/"):
        return "爬虫代码、配置与测试"
    if path.startswith("services/trend-intelligence/"):
        return "原型代码与依赖配置；维护状态由负责人确认"
    if path.startswith("tools/boss-analysis-system/"):
        return "原型代码与依赖配置；维护状态由负责人确认"
    if path.startswith("docs/"):
        return "跨模块代码、Compose、契约与测试"
    return "对应目录代码、配置或项目决策；逐项核对"


def overlap(path: str) -> str:
    """Identify known duplicate topics without claiming semantic equivalence."""

    name = Path(path).name.lower()
    if name in {"baseline.md", "status.md"}:
        return "总体状态"
    if name == "risks.md":
        return "项目级与主后端风险"
    if "system-layer" in name or "unified-site" in name:
        return "总体架构与运行拓扑"
    if "knowledge-graph" in path and "/integrations/" in path:
        return "知识图谱跨服务契约"
    if name == "readme.md" and "matching-service" in path:
        return "架构、API、可靠性、安全与运维混合"
    if name == "readme.md" and path.startswith("爬虫集合/"):
        return "架构、API、配置、运行与合规混合"
    if name == "readme.md" and path.startswith("Extraction/"):
        return "入口、管线、服务与输出契约混合"
    return "无已确认仓库级重复主题"


def action(path: str, doc_type: str) -> tuple[str, str]:
    """Return the reviewed candidate action and target path."""

    old_path = {target: source for source, target in MOVE_TARGETS.items()}.get(path)
    if old_path:
        verb = "archive" if "/archive/" in path else "move"
        return verb, path
    split_roots = (
        "services/matching-service/",
        "services/crawler/",
        "services/jd-extraction/",
        "services/cv-extraction/",
    )
    if path.endswith(".md") and path.startswith(split_roots):
        if "/docs/" in path or path.endswith("/README.md"):
            return "split" if path.endswith("/README.md") or "/docs/" in path else "keep", path
    return "keep", path


def main() -> None:
    """Generate the Markdown table in a stable, reviewable order."""

    rows: list[str] = []
    reverse_moves = {target: source for source, target in MOVE_TARGETS.items()}
    for path in current_documents():
        doc_type, scope, status = classify(path)
        verb, target = action(path, doc_type)
        if path in reverse_moves:
            notes = f"原路径：{reverse_moves[path]}"
        elif verb == "split":
            notes = "由模块 README 主题拆分或作为拆分后入口"
        else:
            notes = "保留最终作用域"
        cells = [
            path,
            doc_type,
            scope,
            status,
            evidence(path, doc_type),
            overlap(path),
            target,
            verb,
            notes,
        ]
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")

    header = """# 文档清单

> 文档类型：清单
> 维护状态：active
> 适用范围：JobPulse
> 事实源：`git ls-files`、各文档正文及表中列出的核对入口
> 责任角色：仓库维护者
> 最后复核：2026-08-20

本清单覆盖 JobPulse 当前受控文档与交付物。“事实源”列表示核对入口，
不代表正文中的每项结论都已确认。`draft` 文档和归档材料不能被活跃索引
当作当前工程事实。

| 当前路径 | 类型 | 作用域 | 当前状态 | 事实源 | 重复主题 | 目标路径 | 动作 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} inventory rows to {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
