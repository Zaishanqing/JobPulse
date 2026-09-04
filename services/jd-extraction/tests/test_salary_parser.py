import pytest

from src.salary_parser import is_standalone_salary_expression, parse_salary


@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "period", "months"),
    [
        ("20-40K", 20000, 40000, "月", None),
        ("20K", 20000, 20000, "月", None),
        ("15-20K·13薪", 15000, 20000, "月", 13),
        ("150-160元/天", 150, 160, "日", None),
        ("400-500元/时", 400, 500, "时", None),
        ("3000-3500元/周", 3000, 3500, "周", None),
        ("2-3万/月", 20000, 30000, "月", None),
        ("8千-1万/月", 8000, 10000, "月", None),
        ("年薪30万", 300000, 300000, "年", None),
        ("月薪8千-1.2万", 8000, 12000, "月", None),
        ("8000元/月", 8000, 8000, "月", None),
    ],
)
def test_parse_salary_uses_one_canonical_unit_system(text, minimum, maximum, period, months):
    salary = parse_salary(text)

    assert salary is not None
    assert salary["minimum"] == minimum
    assert salary["maximum"] == maximum
    assert salary["currency"] == "CNY"
    assert salary["period"] == period
    assert salary["salary_months"] == months
    assert salary["status"] == "specified"


def test_yuan_salary_without_period_is_rejected():
    with pytest.raises(ValueError, match="must declare a period"):
        parse_salary("薪资 300-500元")


@pytest.mark.parametrize(
    ("text", "status"),
    [("面议", "negotiable"), ("薪资可谈", "negotiable"), ("待遇面议", "negotiable"), ("薪资保密", "not_disclosed")],
)
def test_parse_non_numeric_salary_status(text, status):
    assert parse_salary(text) == {"raw_text": text, "status": status}


def test_salary_context_does_not_treat_allowance_amount_as_base_salary():
    assert is_standalone_salary_expression("20-40K")
    assert not is_standalone_salary_expression("补贴 0.2-3K元")


@pytest.mark.parametrize(
    "text",
    [
        "具备15万平方米以上大型园区统筹管理经验",
        "管理10-15万平米生产型园区",
        "服务超过20万用户",
    ],
)
def test_non_currency_wan_quantities_are_not_parsed_as_salary(text):
    assert parse_salary(text) is None


def test_annual_salary_wan_remains_supported():
    assert parse_salary("年薪15万")["period"] == "年"
