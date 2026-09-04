"""Boss 直聘职位详情抓取 (task 02 final).

P0-2: listener 在 navigate 前启动, 使用详情 API target
P0-3: timeout_seconds 用于导航/监听/DOM/body
P0-4: API/DOM/body 三条路径统一经过防伪校验
P0-5: 详情身份与目标岗位一致性校验
P0-6: completed/failed/unavailable 全状态保存 list_payload + detail_payload
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

DETAIL_API_FRAGMENTS = (
    "job/detail",
    "job/detail.json",
    "zpgeek/job/detail",
    "job/card",
)

_DETAIL_DOM_SELECTORS = (
    ".job-sec-text",
    ".job-detail",
    ".job-detail-content",
    "[class*=\"job-detail\"]",
    "[class*=\"job-description\"]",
    ".detail-content",
)

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referrer", "source", "from", "trackingId", "traceId",
    "_t", "timestamp", "ts", "rand", "random", "v", "version",
})

_SENSITIVE_KEYS = frozenset({
    "cookie", "cookies", "authorization", "token", "access_token",
    "refresh_token", "csrf", "csrf_token", "x-csrf-token",
    "phone", "mobile", "telephone", "id_card", "idNumber",
})

MIN_RAW_TEXT_LENGTH = 100
MAX_DETAIL_TIMEOUT = 30
MAX_DETAIL_RETRIES = 3

# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass
class BossJobDetailResult:
    status: Literal["completed", "failed", "unavailable"]
    raw_text: str = ""
    raw_html: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    source_version: str = "1"
    error_code: str = ""
    error_message: str = ""
    detail_extraction_method: str = ""  # api | dom | body_fallback | none


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def build_boss_detail_url(
    encrypt_job_id: str,
    security_id: str | None = None,
    lid: str | None = None,
) -> str:
    if not encrypt_job_id:
        return ""
    url = f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
    params = []
    if security_id:
        params.append(f"securityId={security_id}")
    if lid:
        params.append(f"lid={lid}")
    if params:
        url += "?" + "&".join(params)
    return url


def _normalize_url_for_id(url: str) -> str:
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    try:
        parsed = urlparse(url)
        if parsed.query:
            p = parse_qs(parsed.query, keep_blank_values=False)
            kept = {k: v for k, v in p.items() if k not in _TRACKING_PARAMS}
            clean = urlencode(kept, doseq=True) if kept else ""
            parsed = parsed._replace(query=clean, fragment="")
        lowered = parsed._replace(scheme=parsed.scheme.lower(),
                                  netloc=parsed.netloc.lower())
        return urlunparse(lowered)
    except Exception:
        return url.strip().lower()


def _extract_source_id_from_payload(payload: dict) -> str:
    for path in (
        ("encryptJobId",),
        ("jobId",),
        ("positionId",),
        ("securityId",),
        ("jobInfo", "encryptJobId"),
        ("jobInfo", "jobId"),
        ("zpData", "jobDetail", "encryptJobId"),
        ("zpData", "jobDetail", "jobId"),
    ):
        node = payload
        try:
            for key in path:
                node = node[key]
            if node:
                return str(node)
        except (KeyError, TypeError):
            continue
    return ""


def sanitize_raw_payload(obj: Any) -> Any:
    """递归移除敏感字段。"""
    if isinstance(obj, dict):
        return {
            k: sanitize_raw_payload(v)
            for k, v in obj.items()
            if k.lower() not in _SENSITIVE_KEYS
        }
    if isinstance(obj, list):
        return [sanitize_raw_payload(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# 防伪校验
# ---------------------------------------------------------------------------


def validate_boss_raw_text(
    raw_text: str,
    *,
    job_title: str = "",
    benefits_raw: str = "",
    skills_raw: str = "",
) -> tuple[bool, str]:
    """检查 raw_text 是真实 JD 而非福利/技能/卡片。"""
    if not raw_text or not raw_text.strip():
        return False, "raw_text is empty"
    stripped = raw_text.strip()
    if len(stripped) < MIN_RAW_TEXT_LENGTH:
        return False, f"raw_text too short ({len(stripped)} < {MIN_RAW_TEXT_LENGTH})"
    if benefits_raw and benefits_raw.strip():
        b_words = set(re.split(r'\s+', benefits_raw))
        t_words = set(re.split(r'\s+', stripped))
        overlap = b_words & t_words
        if len(overlap) > 0 and len(t_words - b_words) < 15:
            return False, "raw_text appears to be benefits only"
    if skills_raw and skills_raw.strip():
        s_words = set(re.split(r'[\s,，/]+', skills_raw))
        t_words = set(re.split(r'\s+', stripped))
        overlap = s_words & t_words
        if len(overlap) > 0 and len(t_words - s_words) < 15:
            return False, "raw_text appears to be skills list only"
    if job_title and stripped == job_title.strip():
        return False, "raw_text is identical to job title"
    return True, ""


# ---------------------------------------------------------------------------
# 身份校验
# ---------------------------------------------------------------------------


def validate_boss_detail_identity(
    *,
    expected_source_record_id: str,
    expected_job_title: str,
    detail_payload: dict,
    extracted_title: str = "",
) -> tuple[bool, str]:
    """确认详情响应属于目标岗位。"""
    payload_id = _extract_source_id_from_payload(detail_payload)
    if payload_id:
        if payload_id == expected_source_record_id:
            return True, ""
        # Also check a few platform-equivalent forms
        if (expected_source_record_id and
                len(payload_id) == len(expected_source_record_id) and
                payload_id.lower() == expected_source_record_id.lower()):
            return True, ""
        return False, f"detail_identity_mismatch: payload_id={payload_id!r} != expected={expected_source_record_id!r}"
    title = (extracted_title or "").strip()
    expected = (expected_job_title or "").strip()
    if title and expected:
        if title == expected:
            return True, ""
        t_norm = re.sub(r'\s+', '', title.lower())
        e_norm = re.sub(r'\s+', '', expected.lower())
        if t_norm == e_norm:
            return True, ""
        return False, f"detail_title_mismatch: {title!r} != {expected!r}"
    return False, "detail_identity_unverifiable: no ID or title in response"


# ---------------------------------------------------------------------------
# 统一完成构建 (P0-4/P0-6)
# ---------------------------------------------------------------------------


def _validate_and_build_completed(
    *,
    raw_text: str,
    raw_html: str,
    raw_payload: dict,
    list_payload: dict,
    source_url: str,
    extraction_method: str,
    job_title_raw: str,
    benefits_raw: str,
    skills_raw: str,
) -> BossJobDetailResult:
    if not source_url:
        return BossJobDetailResult(
            status="failed", error_code="source_url_missing",
            error_message="detail URL is empty",
            raw_payload={"list_payload": list_payload, "detail_payload": raw_payload},
            raw_html=raw_html,
        )
    valid, reason = validate_boss_raw_text(
        raw_text, job_title=job_title_raw,
        benefits_raw=benefits_raw, skills_raw=skills_raw,
    )
    if not valid:
        return BossJobDetailResult(
            status="failed", error_code="detail_text_too_short",
            error_message=reason, raw_text=raw_text, raw_html=raw_html,
            raw_payload={"list_payload": list_payload, "detail_payload": raw_payload},
            source_url=source_url,
        )
    return BossJobDetailResult(
        status="completed", raw_text=raw_text, raw_html=raw_html,
        raw_payload={
            "list_payload": sanitize_raw_payload(list_payload),
            "detail_payload": sanitize_raw_payload(raw_payload),
            "detail_extraction_method": extraction_method,
        },
        source_url=source_url,
        source_version="1",
        detail_extraction_method=extraction_method,
    )


def _build_failed(
    *,
    error_code: str,
    error_message: str,
    list_payload: dict,
    detail_payload: dict = None,
    raw_html: str = "",
    source_url: str = "",
    extraction_method: str = "",
) -> BossJobDetailResult:
    return BossJobDetailResult(
        status="failed", error_code=error_code, error_message=error_message,
        raw_payload={
            "list_payload": sanitize_raw_payload(list_payload),
            "detail_payload": sanitize_raw_payload(detail_payload or {}),
            "detail_extraction_method": extraction_method or "none",
            "detail_error_code": error_code,
        },
        raw_html=raw_html, source_url=source_url,
        detail_extraction_method=extraction_method or "none",
    )


# ---------------------------------------------------------------------------
# API 提取
# ---------------------------------------------------------------------------


def _extract_jd_from_api(
    api_body: dict,
    *,
    expected_source_record_id: str,
    expected_job_title: str,
    job_title_raw: str,
    benefits_raw: str,
    skills_raw: str,
    list_payload: dict,
    detail_url: str,
) -> BossJobDetailResult | None:
    """Try to extract JD from a detail API response. Returns None if no match."""
    # Check identity
    ok, reason = validate_boss_detail_identity(
        expected_source_record_id=expected_source_record_id,
        expected_job_title=expected_job_title,
        detail_payload=api_body,
    )
    if not ok:
        return _build_failed(
            error_code=reason.replace("detail_", ""),  # short form
            error_message=reason,
            list_payload=list_payload,
            detail_payload=api_body,
            source_url=detail_url,
            extraction_method="api",
        )

    for path in (
        ("zpData", "jobDetail"),
        ("data", "jobDetail"),
        ("data",),
        ("jobInfo",),
    ):
        node = api_body
        try:
            for key in path:
                node = node.get(key, {})
            if isinstance(node, dict):
                for tk in ("jobDescription", "description", "detail", "jdContent", "content"):
                    text = node.get(tk, "")
                    if isinstance(text, str) and text.strip():
                        return _validate_and_build_completed(
                            raw_text=text, raw_html="",
                            raw_payload=api_body, list_payload=list_payload,
                            source_url=detail_url, extraction_method="api",
                            job_title_raw=job_title_raw,
                            benefits_raw=benefits_raw, skills_raw=skills_raw,
                        )
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# DOM 提取
# ---------------------------------------------------------------------------


def _extract_jd_from_dom(
    page: Any,
    *,
    detail_url: str,
    job_title_raw: str,
    benefits_raw: str,
    skills_raw: str,
    list_payload: dict,
    timeout_seconds: int,
    expected_source_record_id: str = "",
    expected_job_title: str = "",
    navigation_identity_verified: bool = False,
) -> BossJobDetailResult | None:
    if not hasattr(page, 'ele'):
        return None
    # Try title first for identity check
    page_title = _extract_page_job_title(page, timeout_seconds)
    identity_verified = navigation_identity_verified

    for sel in _DETAIL_DOM_SELECTORS:
        try:
            el = page.ele(sel, timeout=min(timeout_seconds, 5))
            if el:
                text = el.text.strip()
                # Step 4: DOM identity check
                if not identity_verified and (expected_source_record_id or expected_job_title):
                    ok, _ = validate_boss_detail_identity(
                        expected_source_record_id=expected_source_record_id,
                        expected_job_title=expected_job_title,
                        detail_payload={}, extracted_title=page_title,
                    )
                    if not ok:
                        continue  # try next selector
                    identity_verified = True

                raw_html = page.html if hasattr(page, 'html') else ""
                valid, reason = validate_boss_raw_text(
                    text, job_title=job_title_raw,
                    benefits_raw=benefits_raw, skills_raw=skills_raw,
                )
                if valid:
                    return _validate_and_build_completed(
                        raw_text=text, raw_html=raw_html,
                        raw_payload={}, list_payload=list_payload,
                        source_url=detail_url, extraction_method="dom",
                        job_title_raw=job_title_raw,
                        benefits_raw=benefits_raw, skills_raw=skills_raw,
                    )
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Body fallback
# ---------------------------------------------------------------------------


def _extract_jd_from_body(
    page: Any,
    *,
    detail_url: str,
    job_title_raw: str,
    benefits_raw: str,
    skills_raw: str,
    list_payload: dict,
    timeout_seconds: int,
    expected_source_record_id: str = "",
    expected_job_title: str = "",
    navigation_identity_verified: bool = False,
) -> BossJobDetailResult | None:
    # Step 4: body identity check
    identity_verified = navigation_identity_verified
    if not identity_verified and (expected_source_record_id or expected_job_title):
        page_title = _extract_page_job_title(page, timeout_seconds)
        ok, _ = validate_boss_detail_identity(
            expected_source_record_id=expected_source_record_id,
            expected_job_title=expected_job_title,
            detail_payload={}, extracted_title=page_title,
        )
        if not ok:
            return None
    try:
        if not hasattr(page, 'ele'):
            return None
        body_el = page.ele('tag:body', timeout=min(timeout_seconds, 5))
        if not body_el:
            return None
        body_text = body_el.text
        raw_html = page.html if hasattr(page, 'html') else ""
    except Exception:
        return None

    body_text = re.sub(r'\n{3,}', '\n\n', body_text)
    for marker in ("公司介绍", "公司信息", "数据来源", "推荐企业",
                   "相关推荐", "猜你喜欢", "热门城市", "Boss直聘", "聊天"):
        idx = body_text.find(marker, 200)
        if idx > 0:
            body_text = body_text[:idx]
    lines = body_text.split('\n')
    kept = []
    for line in lines:
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if s in ('举报', '收藏', '分享', '投递', '聊天', '立即沟通'):
            continue
        if '在线' in s and ('已认证' in s or 'BOSS' in s):
            continue
        kept.append(line)
    body_text = '\n'.join(kept)

    return _validate_and_build_completed(
        raw_text=body_text, raw_html=raw_html, raw_payload={},
        list_payload=list_payload, source_url=detail_url,
        extraction_method="body_fallback",
        job_title_raw=job_title_raw,
        benefits_raw=benefits_raw, skills_raw=skills_raw,
    )


# ---------------------------------------------------------------------------
# Listener lifecycle (Step 5)
# ---------------------------------------------------------------------------


def _reset_detail_listener(page: Any) -> None:
    listen = getattr(page, "listen", None)
    if listen is None:
        return
    if hasattr(listen, "reset"):
        listen.reset()
    elif hasattr(listen, "stop"):
        listen.stop()


def _start_detail_listener(page: Any) -> None:
    listen = getattr(page, "listen", None)
    if listen is None or not hasattr(listen, "start"):
        return
    try:
        listen.start(list(DETAIL_API_FRAGMENTS))
    except TypeError:
        pattern = "|".join(re.escape(x) for x in DETAIL_API_FRAGMENTS)
        listen.start(pattern)


# ---------------------------------------------------------------------------
# URL / title helpers (Steps 3+4)
# ---------------------------------------------------------------------------

_BLOCKED_URL_KEYWORDS = (
    "login", "captcha", "verify", "security-check", "passport",
    "user/login", "web/geek/jobs", "user/register",
)

_DETAIL_TITLE_SELECTORS = (
    "h1", ".job-title", ".name", "[class*='job-title']",
)


def _get_current_page_url(page: Any) -> str:
    return str(getattr(page, "url", "") or "").strip()


class NavigationIdentityResult:
    __slots__ = ("status", "reason")
    def __init__(self, status: str, reason: str = "") -> None:
        self.status = status  # "verified" | "unverifiable" | "blocked" | "mismatch"
        self.reason = reason


def validate_boss_navigation_identity(
    *,
    current_url: str,
    expected_detail_url: str,
    expected_source_record_id: str,
) -> NavigationIdentityResult:
    """Verify the current page matches the target job."""
    if not current_url:
        return NavigationIdentityResult("unverifiable", "current_url is empty")
    lowered = current_url.lower()
    for kw in _BLOCKED_URL_KEYWORDS:
        if kw in lowered:
            return NavigationIdentityResult("blocked", f"blocked page: {kw}")
    if expected_source_record_id and expected_source_record_id in current_url:
        return NavigationIdentityResult("verified", "")
    from urllib.parse import urlparse, urlunparse
    def _norm(u: str) -> str:
        p = urlparse(u)
        return urlunparse(p._replace(query="", fragment="")).lower()
    if _norm(lowered) == _norm(expected_detail_url.lower()):
        return NavigationIdentityResult("verified", "")
    return NavigationIdentityResult("mismatch", "URL does not match expected detail URL")


def _extract_page_job_title(page: Any, timeout_seconds: int) -> str:
    if not hasattr(page, 'ele'):
        return ""
    for sel in _DETAIL_TITLE_SELECTORS:
        try:
            el = page.ele(sel, timeout=min(timeout_seconds, 3))
            if el:
                return (el.text or "").strip()
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Main fetch function (Steps 3+4+5: navigation safety + listener + identity)
# ---------------------------------------------------------------------------


def fetch_boss_job_detail(
    page: Any,
    *,
    source_record_id: str,
    security_id: str | None = None,
    source_url: str | None = None,
    encrypt_job_id: str = "",
    lid: str | None = None,
    job_title_raw: str = "",
    company_name_raw: str = "",
    benefits_raw: str = "",
    skills_raw: str = "",
    list_payload: dict | None = None,
    timeout_seconds: int = MAX_DETAIL_TIMEOUT,
    max_retries: int = MAX_DETAIL_RETRIES,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BossJobDetailResult:
    if not encrypt_job_id and not source_record_id:
        return _build_failed(
            error_code="source_record_id_missing",
            error_message="encrypt_job_id and source_record_id are both empty",
            list_payload=list_payload or {},
        )
    detail_url = source_url or build_boss_detail_url(
        encrypt_job_id or source_record_id, security_id=security_id, lid=lid,
    )
    if not detail_url:
        return _build_failed(
            error_code="detail_url_missing",
            error_message="could not construct detail URL",
            list_payload=list_payload or {},
        )
    lp = list_payload or {}

    for attempt in range(max_retries):
        detail_payload: dict = {}
        listener_error = ""
        raw_html = ""
        _reset_detail_listener(page)
        _start_detail_listener(page)
        try:
            # --- navigate (Step 3: fail fast, no stale DOM/body) ---
            try:
                page.get(detail_url, timeout=timeout_seconds)
            except Exception as exc:
                return _build_failed(
                    error_code="detail_navigation_failed",
                    error_message=str(exc),
                    list_payload=lp, source_url=detail_url,
                    extraction_method="none",
                )

            # --- URL identity check ---
            current_url = _get_current_page_url(page)
            nav = validate_boss_navigation_identity(
                current_url=current_url,
                expected_detail_url=detail_url,
                expected_source_record_id=source_record_id,
            )
            # blocked/mismatch → immediate failure (Step 4)
            if nav.status == "blocked":
                return _build_failed(
                    error_code="detail_navigation_blocked",
                    error_message=nav.reason,
                    list_payload=lp, source_url=detail_url,
                    extraction_method="none",
                )
            if nav.status == "mismatch":
                return _build_failed(
                    error_code="detail_navigation_mismatch",
                    error_message=nav.reason,
                    list_payload=lp, source_url=detail_url,
                    extraction_method="none",
                )
            nav_verified = nav.status == "verified"

            # --- wait API (only if not blocked/mismatch) ---
            if hasattr(page, 'listen') and hasattr(page.listen, 'wait'):
                try:
                    resp = page.listen.wait(timeout=timeout_seconds)
                    if resp and hasattr(resp, 'response'):
                        body = resp.response.body
                        if isinstance(body, str):
                            try:
                                detail_payload = json.loads(body)
                            except Exception:
                                detail_payload = {"raw": body}
                        elif isinstance(body, dict):
                            detail_payload = body
                except Exception as listener_exc:
                    # Listener failure is a recoverable detail-source miss.
                    # DOM/body may still provide the same page's JD after URL
                    # identity has been checked, so retain the error in
                    # provenance instead of aborting or silently swallowing it.
                    listener_error = type(listener_exc).__name__

            if hasattr(page, 'html'):
                raw_html = page.html

            # --- API ---
            if detail_payload:
                result = _extract_jd_from_api(
                    detail_payload,
                    expected_source_record_id=source_record_id,
                    expected_job_title=job_title_raw,
                    job_title_raw=job_title_raw, benefits_raw=benefits_raw,
                    skills_raw=skills_raw, list_payload=lp, detail_url=detail_url,
                )
                if result is not None:
                    return result

            # --- DOM ---
            if hasattr(page, 'ele'):
                result = _extract_jd_from_dom(
                    page, detail_url=detail_url, job_title_raw=job_title_raw,
                    benefits_raw=benefits_raw, skills_raw=skills_raw,
                    list_payload=lp, timeout_seconds=timeout_seconds,
                    expected_source_record_id=source_record_id,
                    expected_job_title=job_title_raw,
                    navigation_identity_verified=nav_verified,
                )
                if result is not None:
                    result.raw_payload["detail_payload"] = sanitize_raw_payload(detail_payload)
                    if listener_error:
                        result.raw_payload["listener_error"] = listener_error
                    result.raw_html = raw_html
                    return result

            # --- body ---
            result = _extract_jd_from_body(
                page, detail_url=detail_url, job_title_raw=job_title_raw,
                benefits_raw=benefits_raw, skills_raw=skills_raw,
                list_payload=lp, timeout_seconds=timeout_seconds,
                expected_source_record_id=source_record_id,
                expected_job_title=job_title_raw,
                navigation_identity_verified=nav_verified,
            )
            if result is not None:
                result.raw_payload["detail_payload"] = sanitize_raw_payload(detail_payload)
                if listener_error:
                    result.raw_payload["listener_error"] = listener_error
                result.raw_html = raw_html
                return result

        finally:
            _reset_detail_listener(page)

        if attempt < max_retries - 1:
            sleep_fn(min(2 ** attempt * 2, 15))

    return _build_failed(
        error_code="detail_max_retries_exceeded",
        error_message=f"failed after {max_retries} retries",
        list_payload=lp, detail_payload=detail_payload,
        raw_html=raw_html, source_url=detail_url,
    )
