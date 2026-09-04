from __future__ import annotations

from pathlib import Path

from scripts.build_release_manifest import migration_heads, sha256_file


def test_sha256_file_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "artifact.txt"
    path.write_text("release", encoding="utf-8")
    assert sha256_file(path) == "a4d451ec23463726f72c43d64c710968f6b602cd653b4de8adee1b556240a829"


def test_migration_heads_are_file_based_and_include_matching() -> None:
    heads = migration_heads(Path(__file__).resolve().parents[1])
    assert "main" in heads
    assert "matching" in heads
    assert heads["matching"]
