from __future__ import annotations

import re


_NUMBER = r"\d+(?:\.\d+)?"
_UNIT = r"[Kk]|万|千|元"
_SEPARATOR = r"[-~—–至]"
_PERIOD = r"月|个月|天|日|时|小时|周|年"
_CONTEXT = r"基础薪资|年薪|月薪|日薪|时薪|周薪|薪资|工资|薪酬"
_NON_SALARY_QUANTITY_SUFFIX = (
    r"平方米|平米|㎡|亩|公里|千米|米|吨|人次|用户|人|台|辆|件|套|次|家|户|份|单|条"
)

SALARY_PATTERN = re.compile(
    rf"(?P<prefix>{_CONTEXT})?\s*[:：]?\s*"
    rf"(?P<minimum>{_NUMBER})\s*(?:(?P<minimum_unit>{_UNIT})\s*)?"
    rf"(?:{_SEPARATOR}\s*(?P<maximum>{_NUMBER})\s*(?P<maximum_unit>{_UNIT}))"
    rf"(?!\s*(?:{_NON_SALARY_QUANTITY_SUFFIX}))"
    rf"(?:\s*(?:/|每)\s*(?P<period>{_PERIOD}))?"
    rf"(?:\s*(?:[·xX*]\s*)?(?P<months>{_NUMBER})\s*薪)?"
    rf"|"
    rf"(?P<single_prefix>{_CONTEXT})?\s*[:：]?\s*"
    rf"(?P<single>{_NUMBER})\s*(?P<single_unit>{_UNIT})"
    rf"(?!\s*(?:{_NON_SALARY_QUANTITY_SUFFIX}))"
    rf"(?:\s*(?:/|每)\s*(?P<single_period>{_PERIOD}))?"
    rf"(?:\s*(?:[·xX*]\s*)?(?P<single_months>{_NUMBER})\s*薪)?"
)

NEGOTIABLE_PATTERN = re.compile(r"薪资可谈|工资可谈|薪酬可谈|待遇面议|薪资面议|工资面议|薪酬面议|面议")
NOT_DISCLOSED_PATTERN = re.compile(r"薪资保密|工资保密|薪酬保密|待遇保密|保密")
SALARY_CONTEXT_PATTERN = re.compile(r"基础薪资|薪资|工资|薪酬|月薪|年薪|日薪|时薪|周薪|待遇")

PERIOD_MAP = {
    "月": "月",
    "个月": "月",
    "天": "日",
    "日": "日",
    "时": "时",
    "小时": "时",
    "周": "周",
    "年": "年",
}
PREFIX_PERIOD_MAP = {
    "月薪": "月",
    "日薪": "日",
    "时薪": "时",
    "周薪": "周",
    "年薪": "年",
}


def _to_cny(value: str, unit: str) -> float:
    multiplier = {"K": 1000, "k": 1000, "千": 1000, "万": 10000, "元": 1}[unit]
    return float(value) * multiplier


def _number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _parse_numeric(match: re.Match[str], *, single: bool) -> dict:
    if single:
        minimum_text = match.group("single")
        minimum_unit = match.group("single_unit")
        maximum_text = minimum_text
        maximum_unit = minimum_unit
        prefix = match.group("single_prefix")
        period_token = match.group("single_period")
        months_text = match.group("single_months")
    else:
        minimum_text = match.group("minimum")
        minimum_unit = match.group("minimum_unit")
        maximum_text = match.group("maximum")
        maximum_unit = match.group("maximum_unit")
        prefix = match.group("prefix")
        period_token = match.group("period")
        months_text = match.group("months")

    if minimum_unit is None:
        minimum_unit = maximum_unit
    if maximum_unit is None:
        maximum_unit = minimum_unit
    minimum = _to_cny(minimum_text, minimum_unit)
    maximum = _to_cny(maximum_text, maximum_unit)
    if minimum > maximum:
        raise ValueError(f"Salary minimum must not exceed maximum: {match.group(0)}")

    period = PERIOD_MAP.get(period_token) if period_token else PREFIX_PERIOD_MAP.get(prefix)
    if period is None and minimum_unit in {"K", "k"}:
        period = "月"
    if period is None:
        raise ValueError(f"Salary with {minimum_unit} unit must declare a period or salary context: {match.group(0)}")

    result = {
        "raw_text": match.group(0),
        "status": "specified",
        "minimum": _number(minimum),
        "maximum": _number(maximum),
        "currency": "CNY",
        "period": period,
        "salary_months": None,
    }
    if months_text is not None:
        months = float(months_text)
        if months < 1:
            raise ValueError(f"salary_months must be at least 1: {match.group(0)}")
        result["salary_months"] = _number(months)
    return result


def parse_salary(jd_text: str) -> dict | None:
    negotiable = NEGOTIABLE_PATTERN.search(jd_text)
    not_disclosed = NOT_DISCLOSED_PATTERN.search(jd_text)
    numeric = SALARY_PATTERN.search(jd_text)
    if negotiable is not None and (numeric is None or negotiable.start() <= numeric.start()):
        return {"raw_text": negotiable.group(0), "status": "negotiable"}
    if not_disclosed is not None and (numeric is None or not_disclosed.start() <= numeric.start()):
        return {"raw_text": not_disclosed.group(0), "status": "not_disclosed"}
    if numeric is None:
        return None
    groups = numeric.groupdict()
    return _parse_numeric(numeric, single=groups.get("single") is not None)


def contains_salary_expression(text: str) -> bool:
    """Return whether text contains a supported numeric or non-numeric salary expression."""
    return (
        SALARY_PATTERN.search(text) is not None
        or NEGOTIABLE_PATTERN.search(text) is not None
        or NOT_DISCLOSED_PATTERN.search(text) is not None
    )


def is_standalone_salary_expression(text: str) -> bool:
    """Return true only when the entire field is a salary expression, not an allowance sentence."""
    value = text.strip()
    return (
        SALARY_PATTERN.fullmatch(value) is not None
        or NEGOTIABLE_PATTERN.fullmatch(value) is not None
        or NOT_DISCLOSED_PATTERN.fullmatch(value) is not None
    )
