"""Crawler 内部文本处理。

Task 02: 生产采集路径只允许确定性文本处理。
语义方法 (split_jd, normalize_salary, normalize_experience, normalize_education)
保留且可被历史测试调用，但生产路径不得依赖它们。
"""

import re
import warnings
from typing import Any

from multi_company_scraper.models.job_data import JobData

# ---------------------------------------------------------------------------
# 确定性文本处理 (生产路径唯一允许的操作)
# ---------------------------------------------------------------------------

_TEXT_CANONICALIZATION_VERSION = "v1"

# Characters illegal in Windows filenames that shouldn't appear in JD text
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _canonicalize_whitespace(text: str) -> str:
    """Normalise runs of whitespace to a single space, keep newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_page_noise(text: str) -> str:
    """Remove boilerplate page-chrome that is clearly not JD content."""
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        # Navigation / footer patterns
        if re.fullmatch(r"[←→↑↓]+\s*.*", stripped):
            continue
        if re.fullmatch(r"上一页|下一页|返回顶部|举报", stripped):
            continue
        if stripped in ("首页", "上一页", "下一页", "末页"):
            continue
        kept.append(line)
    return "\n".join(kept)


def compute_raw_text(html_or_text: str, *, strip_noise: bool = True) -> str:
    """Produce stable ``raw_text`` for an Extraction input.

    Applies only deterministic processing:
    1. Strip control characters
    2. Normalise whitespace
    3. Optionally remove page chrome

    Does NOT split sections, extract skills, or normalise salary/education.
    """
    cleaned = _CTRL_RE.sub("", html_or_text)
    cleaned = _canonicalize_whitespace(cleaned)
    if strip_noise:
        cleaned = _strip_page_noise(cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# 统一公共入口 (供生产路径调用)
# ---------------------------------------------------------------------------


class Normalizer:
    """Crawler-internal text normaliser.

    After task 02, the production path calls :meth:`normalize_raw`.  The
    legacy :meth:`normalize` is kept for test compatibility but no longer
    fills semantic fields.
    """

    @classmethod
    def normalize_raw(cls, raw: dict[str, Any], company_name: str, platform: str) -> JobData:
        """Production entry point — deterministic text processing only.

        Does NOT split JD, extract skills, normalise salary, or classify
        education/experience.  Those operations belong to Extraction.
        """
        jd_text = raw.get("jd_text", "")
        if jd_text:
            jd_text = compute_raw_text(jd_text)
            raw_text_status = "completed"
            raw_text_error = ""
        else:
            raw_text_status = "failed"
            raw_text_error = "jd_text is empty"

        return JobData(
            company_name=company_name,
            job_title=raw.get("job_title", ""),
            source_platform=platform,
            job_id=raw.get("job_id", ""),
            department=raw.get("department", ""),
            city=raw.get("city", ""),
            district=raw.get("district", ""),
            job_type=raw.get("job_type", ""),
            salary_desc=raw.get("salary_desc", ""),
            benefits_raw=raw.get("benefits", ""),
            publish_date=raw.get("publish_date", ""),
            source_url=raw.get("source_url", ""),
            jd_text=jd_text,
            # Website-provided tags are source evidence.  Keep their original
            # text separate from the deprecated semantic ``skill_tags`` field.
            skills_raw=raw.get("skills_raw", raw.get("skill_tags", "")),
            experience_raw=raw.get("experience", ""),
            education_raw=raw.get("education", ""),
            raw_payload=raw.get("raw_payload", {}),
            raw_html=raw.get("raw_html", ""),
            text_canonicalization_version=_TEXT_CANONICALIZATION_VERSION,
            source_version=str(raw.get("source_version") or "1"),
            raw_text_status=raw_text_status,
            raw_text_error=raw_text_error,
            # --- deprecated semantic fields — always empty in production ---
            salary_min=0,
            salary_max=0,
            jd_responsibility="",
            jd_requirement="",
            skill_tags="",
            experience="",
            education="",
        )

    # ====================================================================
    # Legacy methods — kept for historical test compatibility only.
    # Production paths MUST NOT call these.
    # ====================================================================

    # -- salary -----------------------------------------------------------

    SALARY_RANGE_K = re.compile(r"(\d+)\s*[Kk]\s*[-~至到]\s*(\d+)\s*[Kk]")
    SALARY_SINGLE_K = re.compile(r"(\d+)\s*[Kk]")
    SALARY_RANGE_YUAN = re.compile(r"(\d+)\s*[-~至到]\s*(\d+)\s*元")
    SALARY_MONTHS = re.compile(r"[·\*]\s*(\d+)\s*薪")

    EXPERIENCE_MAP: list[tuple[re.Pattern, str]] = [
        (re.compile(r"经验不限|不限"), "不限"),
        (re.compile(r"应届|校招|毕业生"), "1年以下"),
        (re.compile(r"1年.*以下|1年以内"), "1年以下"),
        (re.compile(r"1[-~至到]3|一到三"), "1-3年"),
        (re.compile(r"3[-~至到]5|三到五"), "3-5年"),
        (re.compile(r"5[-~至到]10|五到十"), "5-10年"),
        (re.compile(r"10年.*以上|十年以上"), "10年以上"),
    ]

    EDUCATION_MAP: list[tuple[re.Pattern, str]] = [
        (re.compile(r"不限"), "不限"),
        (re.compile(r"大专|专科"), "大专"),
        (re.compile(r"本科"), "本科"),
        (re.compile(r"硕士|研究生"), "硕士"),
        (re.compile(r"博士"), "博士"),
    ]

    @classmethod
    def normalize_salary(cls, text: str) -> tuple[int, int]:
        """Deprecated: salary normalisation belongs to Extraction."""
        warnings.warn(
            "Normalizer.normalize_salary() is deprecated; salary normalisation "
            "belongs to Extraction.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not text or "面议" in text:
            return (0, 0)
        months = 12
        m = cls.SALARY_MONTHS.search(text)
        if m:
            months = int(m.group(1))
        m = cls.SALARY_RANGE_K.search(text)
        if m:
            return (int(int(m.group(1)) * months / 12), int(int(m.group(2)) * months / 12))
        m = cls.SALARY_SINGLE_K.search(text)
        if m:
            v = int(int(m.group(1)) * months / 12)
            return (v, v)
        m = cls.SALARY_RANGE_YUAN.search(text)
        if m:
            return (int(int(m.group(1)) / 1000), int(int(m.group(2)) / 1000))
        return (0, 0)

    @classmethod
    def normalize_experience(cls, text: str) -> str:
        """Deprecated: experience classification belongs to Extraction."""
        warnings.warn(
            "Normalizer.normalize_experience() is deprecated; experience "
            "classification belongs to Extraction.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not text:
            return ""
        for pattern, result in cls.EXPERIENCE_MAP:
            if pattern.search(text):
                return result
        return ""

    @classmethod
    def normalize_education(cls, text: str) -> str:
        """Deprecated: education classification belongs to Extraction."""
        warnings.warn(
            "Normalizer.normalize_education() is deprecated; education "
            "classification belongs to Extraction.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not text:
            return ""
        for pattern, result in cls.EDUCATION_MAP:
            if pattern.search(text):
                return result
        return ""

    # -- JD splitting ----------------------------------------------------

    _RESP_START_MARKERS = [
        "【岗位职责】", "岗位职责", "【职位描述】", "职位描述",
        "【工作职责】", "工作职责", "【工作内容】", "工作内容", "职责描述",
        "【岗位概述】", "岗位概述", "你将参与", "职位介绍",
        "【工作内容与职责】", "工作内容与职责",
    ]
    _REQ_START_MARKERS = [
        "【任职要求】", "任职要求", "【任职资格】", "任职资格",
        "【岗位要求】", "岗位要求", "【职位要求】", "职位要求",
        "我们希望你", "我们期待你", "【能力要求】", "能力要求", "技能要求",
        "如果你具备以下背景，会更匹配",
        "【应聘条件】", "应聘条件", "【录用条件】", "录用条件",
        "核心能力方向",
    ]
    _SECTION_END_MARKERS = [
        "投递方式", "申请方式", "联系方式", "工作地址", "公司地址",
        "公司介绍", "关于我们", "福利待遇", "薪资范围", "补充说明",
        "其他要求", "工作地点", "所属部门",
        "其他信息", "语言要求", "行业要求", "公司简介", "猎聘温馨提示",
        "查看全部", "猜你喜欢", "公司信息", "数据来源", "推荐企业",
        "相关推荐", "相关公司", "热门城市", "热门招聘", "当前位置",
        "注册时间", "经营范围", "融资阶段", "人数规模", "职位地址",
        "搜索", "关注", "发布时间", "职能类别",
        "加分方向", "加分项", "我们提供",
    ]

    @classmethod
    def _find_earliest_marker(cls, text: str, markers: list[str]) -> tuple[int, str]:
        best_idx = len(text)
        best_marker = ""
        for m in markers:
            idx = text.find(m)
            if 0 <= idx < best_idx:
                best_idx = idx
                best_marker = m
        return best_idx, best_marker

    @classmethod
    def split_jd(cls, text: str) -> tuple[str, str]:
        """Deprecated: JD splitting belongs to Extraction."""
        warnings.warn(
            "Normalizer.split_jd() is deprecated; JD section splitting belongs "
            "to Extraction.",
            DeprecationWarning,
            stacklevel=2,
        )
        # ... (implementation unchanged for test compat)
        if not text:
            return ("", "")
        text = re.sub(r'\s+', ' ', text).strip()
        resp_start, resp_marker = cls._find_earliest_marker(text, cls._RESP_START_MARKERS)
        req_start, req_marker = cls._find_earliest_marker(text, cls._REQ_START_MARKERS)

        resp = ""
        if resp_start < len(text):
            start = resp_start + len(resp_marker)
            end_candidates = []
            if req_start < len(text) and req_start > resp_start:
                end_candidates.append(req_start)
            for m in cls._SECTION_END_MARKERS:
                idx = text.find(m, start)
                if idx >= 0:
                    end_candidates.append(idx)
            end = min(end_candidates) if end_candidates else len(text)
            resp = text[start:end].strip().lstrip("：:。.，,、 \t\n")
        elif req_start < len(text):
            resp = text[:req_start].strip()
            for pfx in ["岗位职责：", "岗位职责:", "职位描述：", "职位描述:",
                        "工作职责：", "工作职责:", "工作内容：", "工作内容:"]:
                if resp.startswith(pfx):
                    resp = resp[len(pfx):].strip()

        req = ""
        if req_start < len(text):
            start = req_start + len(req_marker)
            end_candidates = []
            for m in cls._SECTION_END_MARKERS:
                idx = text.find(m, start)
                if idx >= 0:
                    end_candidates.append(idx)
            end = min(end_candidates) if end_candidates else len(text)
            req = text[start:end].strip().lstrip("：:。.，,、 \t\n")

        resp = cls._clean_jd_section(resp)
        req = cls._clean_jd_section(req)

        if not resp and not req:
            for kw in cls._REQ_START_MARKERS:
                idx = text.find(kw)
                if idx > 0:
                    resp_part = text[:idx].strip()
                    for pfx in ["岗位职责：", "岗位职责:", "职位描述：", "职位描述:",
                                "工作职责：", "工作职责:"]:
                        if resp_part.startswith(pfx):
                            resp_part = resp_part[len(pfx):]
                    req_part = text[idx + len(kw):].strip().lstrip("：:")
                    return (cls._clean_jd_section(resp_part),
                            cls._clean_jd_section(req_part))
            return (cls._clean_jd_section(text), "")
        return (resp, req)

    @classmethod
    def _clean_jd_section(cls, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'^[\s：:。.，,、\-•·●\*\]】]+\s*', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        if len(text) < 2:
            return ""
        return text

    # -- legacy normalize (test compat only) -----------------------------

    @classmethod
    def normalize(cls, raw: dict, company_name: str, platform: str) -> JobData:
        """Deprecated: use :meth:`normalize_raw` for production paths.

        This method is kept for historical test compatibility.  It delegates
        to :meth:`normalize_raw` and does NOT fill semantic fields in
        production.
        """
        warnings.warn(
            "Normalizer.normalize() is deprecated; use Normalizer.normalize_raw() "
            "for production paths.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.normalize_raw(raw, company_name, platform)
