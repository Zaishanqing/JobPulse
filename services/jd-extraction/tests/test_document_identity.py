from pathlib import Path

from src.application.identity import build_document_id, build_offline_document_id

def test_document_id_is_deterministic_for_same_source_version():
    first = build_document_id("boss_zhipin", "job-1", "1")
    second = build_document_id("boss_zhipin", "job-1", "1")
    assert first == second
    assert first == "boss_zhipin:job-1:1"


def test_document_id_changes_with_source_version():
    assert build_document_id("boss_zhipin", "job-1", "1") != build_document_id(
        "boss_zhipin", "job-1", "2"
    )


def test_document_id_changes_with_each_source_identity_component():
    base = build_document_id("boss_zhipin", "job-1", "1")
    assert base != build_document_id("liepin", "job-1", "1")
    assert base != build_document_id("boss_zhipin", "job-2", "1")


def test_offline_document_id_is_stable_across_file_copies(tmp_path: Path):
    first = tmp_path / "first.xlsx"
    second = tmp_path / "copied.xlsx"
    first.write_bytes(b"immutable input artifact")
    second.write_bytes(first.read_bytes())

    assert build_offline_document_id("boss_zhipin", first, 7, "same text") != (
        build_offline_document_id("boss_zhipin", second, 7, "same text")
    )


def test_offline_document_id_changes_with_platform_row_or_artifact(tmp_path: Path):
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"
    first.write_bytes(b"artifact-a")
    second.write_bytes(b"artifact-b")
    base = build_offline_document_id("boss_zhipin", first, 1, "same text")

    assert base != build_offline_document_id("liepin", first, 1, "same text")
    assert base != build_offline_document_id("boss_zhipin", first, 2, "same text")
    assert base != build_offline_document_id("boss_zhipin", second, 1, "same text")
