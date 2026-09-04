import pytest
from pydantic import ValidationError

from app.domain.input_limits import MAX_JD_TEXT_CHARS
from app.schemas.jd import JDRawTextUpdate, JDTextCreate


def test_jd_text_create_rejects_oversize_raw_text_at_schema_boundary():
    payload = {"title": "后端工程师", "raw_text": "x" * (MAX_JD_TEXT_CHARS + 1)}
    with pytest.raises(ValidationError):
        JDTextCreate.model_validate(payload)


def test_jd_raw_text_update_rejects_oversize_raw_text_at_schema_boundary():
    with pytest.raises(ValidationError):
        JDRawTextUpdate.model_validate(
            {"raw_text": "x" * (MAX_JD_TEXT_CHARS + 1)}
        )
