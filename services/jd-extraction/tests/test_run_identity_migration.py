from pathlib import Path

from src.run_identity_migration import _replace_exact_ids


def test_replace_exact_ids_updates_identity_values_without_rewriting_text():
    mapping = {"jd_000001": "jdv1_abc"}
    payload = {
        "document_id": "jd_000001",
        "nested": [{"jd_id": "jd_000001"}],
        "description": "evidence mentions jd_000001 as ordinary text",
    }

    assert _replace_exact_ids(payload, mapping) == {
        "document_id": "jdv1_abc",
        "nested": [{"jd_id": "jdv1_abc"}],
        "description": "evidence mentions jd_000001 as ordinary text",
    }


def test_replace_exact_ids_does_not_modify_paths_or_non_strings(tmp_path: Path):
    path = tmp_path / "jd_000001.json"
    payload = {"path": path, "row_index": 1}

    assert _replace_exact_ids(payload, {"jd_000001": "jdv1_abc"}) == payload
