import pytest
import yaml
from pydantic import ValidationError

from src.models import Salary


def test_salary_contract_accepts_negotiable_without_numeric_fields():
    assert Salary(raw_text="面议", status="negotiable").model_dump() == {
        "raw_text": "面议",
        "status": "negotiable",
        "minimum": None,
        "maximum": None,
        "currency": None,
        "period": None,
        "salary_months": None,
    }


def test_salary_contract_rejects_partial_specified_value():
    with pytest.raises(ValidationError, match="specified salary requires"):
        Salary(raw_text="20K", status="specified", minimum=20000)


def test_salary_contract_rejects_numeric_fields_for_non_numeric_status():
    with pytest.raises(ValidationError, match="must not contain numeric"):
        Salary(raw_text="面议", status="negotiable", minimum=1, maximum=1)


def test_salary_contract_rejects_negative_amounts_and_invalid_month_count():
    with pytest.raises(ValidationError):
        Salary(raw_text="-1K", minimum=-1000, maximum=1000, currency="CNY", period="月")
    with pytest.raises(ValidationError):
        Salary(raw_text="20K·0薪", minimum=20000, maximum=20000, currency="CNY", period="月", salary_months=0)


def test_external_contract_lists_same_salary_values():
    contract = yaml.safe_load(open("config/field_contract.yaml", encoding="utf-8"))
    assert contract["canonical_enums"]["salary_status"] == ["specified", "negotiable", "not_disclosed"]
    assert contract["canonical_enums"]["salary_currency"] == ["CNY"]
    assert contract["canonical_enums"]["salary_period"] == ["月", "日", "时", "周", "年"]
