"""Validate repository-owned Markdown links and documentation boundaries.

The checker is intentionally dependency-free so it can run in local development
and continuous integration. It does not fetch external URLs. Historical archive
files are checked for local target existence, while active documents receive
additional governance checks for moved paths and direct links into archive
evidence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".pnpm-store",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".test-artifacts",
}
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
ABSOLUTE_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/[^<\s])")
VOLATILE_CLAIM_PATTERN = re.compile(
    r"(?:测试通过 \d+|共 \d+ 个测试|准确率[：: ]*\d+|\d+\+ 个测试)"
)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "data:", "tel:")
OLD_PATHS = (
    "docs/integration/BASELINE.md",
    "docs/integration/OFFLINE_JD_BUNDLES.md",
    "docs/integration/PATH_OWNERSHIP.md",
    "docs/integration/ROOT_COMPOSE.md",
    "框架实现/docs/operations/status.md",
    "框架实现/docs/operations/risks.md",
    "框架实现/docs/governance/documentation-standard.md",
    "框架实现/docs/architecture/system-layer-design.md",
    "框架实现/docs/architecture/system-layer-diagram.md",
    "框架实现/docs/architecture/unified-site-integration.md",
)


def markdown_files() -> list[Path]:
    """Return repository-owned Markdown and MDX files in stable order."""

    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".md", ".mdx"}
        and not any(part in SKIP_PARTS for part in path.parts)
        # This user-owned untracked note predates the task and is outside the
        # governed documentation set.
        and path.name != "本批JD数据质量问题汇总.md"
    )


def split_destination(raw: str) -> str:
    """Extract a Markdown destination while preserving spaces in local paths."""

    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    # Markdown permits an optional quoted title after the destination. Existing
    # repository links do not use unescaped spaces plus titles, so only split
    # when a quote delimiter is present.
    for delimiter in (' "', " '"):
        if delimiter in value:
            return value.split(delimiter, 1)[0]
    return value


def exact_case(path: Path) -> bool:
    """Check each component against directory entries on case-insensitive hosts."""

    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError):
        return False
    current = ROOT.resolve()
    for part in relative.parts:
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return True


def is_archive(path: Path) -> bool:
    """Return whether a document belongs to a historical archive."""

    return (
        "archive" in {part.lower() for part in path.relative_to(ROOT).parts}
        or path.name.lower().startswith("archive-")
    )


def check_flat_docs_layout() -> list[str]:
    """Require every repository docs directory to contain files directly."""

    errors: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        for index, part in enumerate(relative.parts[:-1]):
            if part.lower() == "docs" and len(relative.parts) - index > 2:
                errors.append(
                    f"{relative.as_posix()}: docs directories must use a flat file layout"
                )
                break
    return errors


def check_file(path: Path) -> list[str]:
    """Return link and governance errors for one document."""

    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    relative_source = path.relative_to(ROOT).as_posix()
    active = not is_archive(path)

    if active and relative_source != "docs/document-map.md":
        normalized_text = text.replace("\\", "/")
        for old_path in OLD_PATHS:
            if old_path in normalized_text:
                errors.append(f"{relative_source}: references moved path {old_path}")
        if ABSOLUTE_PATH_PATTERN.search(text):
            errors.append(f"{relative_source}: contains a local absolute path")
        if VOLATILE_CLAIM_PATTERN.search(text):
            errors.append(
                f"{relative_source}: contains a volatile test-count or accuracy claim"
            )

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in LINK_PATTERN.finditer(line):
            raw = split_destination(match.group(1))
            if not raw or raw.startswith("#") or raw.lower().startswith(EXTERNAL_PREFIXES):
                continue
            destination = unquote(raw.split("#", 1)[0].split("?", 1)[0])
            if not destination:
                continue
            candidate = (path.parent / destination).resolve()
            # Historical reports commonly use GitHub-style ``file.py:42`` line
            # suffixes. Strip the suffix only when the literal target is absent.
            if not candidate.exists() and re.search(r":\d+$", destination):
                destination = re.sub(r":\d+$", "", destination)
                candidate = (path.parent / destination).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(
                    f"{relative_source}:{line_number}: local link escapes repository: {raw}"
                )
                continue
            if not candidate.exists():
                # Frozen archives may reference files removed after the event.
                # Rewriting them would corrupt historical evidence, so only
                # active documents fail on missing local targets.
                if active:
                    errors.append(
                        f"{relative_source}:{line_number}: missing relative target: {raw}"
                    )
                continue
            if not exact_case(candidate):
                errors.append(
                    f"{relative_source}:{line_number}: target case differs from filesystem: {raw}"
                )
            if (
                active
                and is_archive(candidate)
                and candidate.name.lower() != "readme.md"
            ):
                errors.append(
                    f"{relative_source}:{line_number}: active document links directly "
                    f"to archive evidence instead of its README: {raw}"
                )
    return errors


def main() -> int:
    """Run all checks and return a process-compatible status code."""

    files = markdown_files()
    errors = check_flat_docs_layout()
    errors.extend(error for path in files for error in check_file(path))
    if errors:
        print(f"Markdown documentation check failed with {len(errors)} error(s):")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Markdown documentation check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
