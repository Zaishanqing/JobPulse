import pytest

from src.exceptions import InputFormatError
from src.preprocess import normalize_jd_text, preprocess_row
from src.text_cleaning import clean_jd_text


def test_preprocess_row_reads_only_explicit_raw_text_field():
    row = {
        "岗位名称": "Agent 工程师",
        "公司名称": "OpenAI China",
        "岗位职责": "这列不应进入模型输入",
        "原始文本": "岗位职责：负责 Agent 系统开发。\n任职要求：熟悉 Python 与 Docker。",
        "jd_id": "custom_jd_2",
    }

    payload, failed_case = preprocess_row(row, row_index=2)

    assert failed_case is None
    assert payload is not None
    assert payload["jd_id"] == "custom_jd_2"
    assert payload["job_title_raw"] == "Agent 工程师"
    assert payload["company"] == "OpenAI China"
    assert payload["jd_text_original"] == row["原始文本"]
    assert payload["jd_text"] == normalize_jd_text(row["原始文本"])
    assert payload["cleaned_text"] == payload["jd_text"]
    assert [block["text"] for block in payload["source_blocks"]] == [
        "岗位职责:负责 Agent 系统开发。",
        "任职要求:熟悉 Python 与 Docker。",
    ]
    for block in payload["source_blocks"]:
        assert payload["jd_text"][block["start"] : block["end"]] == block["text"]
    assert "这列不应进入模型输入" not in payload["jd_text"]


def test_preprocess_row_cleans_platform_watermarks_before_source_blocks():
    original = "岗位职kanzhun责：\n1、定来自BOSS直聘义AI产品"

    payload, failed_case = preprocess_row({"原始文本": original}, row_index=1)

    assert failed_case is None
    assert payload is not None
    expected = clean_jd_text(original)
    assert payload["jd_text_original"] == original
    assert payload["jd_text"] == expected
    assert payload["cleaned_text"] == expected
    assert "kanzhun" not in payload["jd_text"]
    assert "来自BOSS直聘" not in payload["jd_text"]
    for block in payload["source_blocks"]:
        assert payload["jd_text"][block["start"] : block["end"]] == block["text"]


def test_preprocess_row_fails_when_no_raw_text_exists():
    payload, failed_case = preprocess_row(
        {"jd_id": "custom_jd_3", "empty_a": None, "empty_b": "   "},
        row_index=3,
    )

    assert payload is None
    assert failed_case is not None
    assert failed_case["error_type"] == "missing_required_input"


def test_preprocess_row_rejects_multiple_raw_text_fields():
    with pytest.raises(InputFormatError, match="Multiple raw text fields"):
        preprocess_row({"jd_text": "A", "原始文本": "B"}, row_index=1)


def test_preprocess_normalizes_unicode_compatibility_forms_before_source_blocks():
    original = "使⽤ＡＩ①开发，不删除内容。"

    payload, failed_case = preprocess_row({"原始文本": original}, row_index=1)

    assert failed_case is None
    assert payload is not None
    assert payload["jd_text_original"] == original
    assert payload["jd_text"] == "使用AI1开发,不删除内容。"
    assert payload["source_blocks"][0]["text"] == payload["jd_text"]
    block = payload["source_blocks"][0]
    assert payload["jd_text"][block["start"] : block["end"]] == block["text"]
    assert normalize_jd_text(original) == payload["jd_text"]


def test_source_blocks_split_sentences_without_rewriting_text():
    payload, failed_case = preprocess_row(
        {"原始文本": "职责:负责开发;负责测试。\n要求:熟悉 Python!"},
        row_index=1,
    )

    assert failed_case is None
    assert payload is not None
    assert [block["text"] for block in payload["source_blocks"]] == [
        "职责:负责开发;",
        "负责测试。",
        "要求:熟悉 Python!",
    ]
    for block in payload["source_blocks"]:
        assert payload["jd_text"][block["start"] : block["end"]] == block["text"]
