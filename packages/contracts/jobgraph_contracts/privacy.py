"""Shared PII boundary checks for profile payloads crossing service borders.

Copied from matching-service ``app.domain.privacy`` so upstream services can
scrub profiles with the exact same rules that matching-service enforces.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

_FORBIDDEN_KEYS = {
    "full_name", "real_name", "phone", "phone_number", "mobile", "email",
    "address", "home_address", "gender", "birth_date", "birthday", "birth_year",
    "id_card", "identity_number", "wechat", "qq", "contact", "contact_info",
    "contact_name", "person_name", "passport", "passport_number", "national_id",
    "social_account", "username", "whatsapp", "telegram",
}

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_CANDIDATE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s().-]*)?(?:\(\d{1,4}\)|\d{1,4})"
    r"[\s().-]*\d{3,4}[\s.-]*\d{3,4}(?![\w.])"
)
_CHINA_PHONE = re.compile(
    r"(?<![0-9A-Za-z_])(?:\+?86[\s.-]*)?1[3-9](?:[\s.-]*\d){9}"
    r"(?![0-9A-Za-z_])"
)
_CHINA_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
_UUID_TOKEN = re.compile(
    r"(?i)(?<![0-9a-f])"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?![0-9a-f])"
)
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>]+|\b(?:linkedin\.com/in/)[^\s<>]+")
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}(?![\w@])")
_LABELED_CONTACT = re.compile(
    r"(?i)(?:联系(?:方式)?|电话|手机|微信|wechat|qq|telegram|whatsapp|"
    r"社交账号|账号|handle)\s*[:：]?\s*\S+|"
    r"(?:call|text|reach\s+me|contact\s+me|message\s+me)\s+"
    r"(?:(?:me\s+)?(?:at|on)\s+)?\S+"
)
_RELAXED_LABELED_CONTACT = re.compile(
    r"(?i)(?:联系(?:方式)?|电话|手机|微信|wechat|qq|telegram|whatsapp|"
    r"社交账号|账号|handle)\s*[:：]\s*\S+|"
    r"(?:微信|wechat|qq|telegram|whatsapp|社交账号|账号|handle)\s*[:：]?\s*\S+|"
    r"\b(?:call|text|reach|contact|message)\b\s+"
    r"(?:me\s+)?(?:at\s+|on\s+)?\S+"
)
_LABELED_NAME = re.compile(
    r"(?i)(?:姓名|真实姓名|联系人)\s*[:：]\s*[\u4e00-\u9fff·]{2,20}|"
    r"(?:my\s+name\s+is|name|contact\s+person)\s*[:：]?\s*"
    r"[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,3}"
)
_LABELED_ID = re.compile(
    r"(?i)(?:身份证(?:号|号码)?|护照(?:号|号码)?|passport|national\s+id)"
    r"\s*[:：]?\s*[0-9A-Z-]{6,24}"
)
_LABELED_ADDRESS = re.compile(
    r"(?i)(?:地址|住址|居住地|家庭地址|address|residence)\s*[:：]\s*\S.{3,}"
)
_CHINA_ADDRESS = re.compile(
    r"(?:[\u4e00-\u9fff]{2,}(?:省|自治区|市))?"
    r"[\u4e00-\u9fff]{2,}(?:区|县|镇|乡)?[\u4e00-\u9fff0-9]{2,}"
    r"(?:路|街|道|巷|弄|大街)[\u4e00-\u9fff0-9]*\d+(?:号|室|栋|单元)?"
)
_STREET_ADDRESS = re.compile(
    r"(?i)(?<!\w)\d{1,6}[A-Za-z]?\s+[A-Za-z][A-Za-z .'-]{2,50}\s+"
    r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|"
    r"boulevard|blvd\.?)\b"
)


# -- structured identifier fields --------------------------------------------------
# Fields whose values are system identifiers, not free text.  Only these
# curated names receive ID-specific validation; adding a field here is a
# deliberate security decision.

_STRUCTURED_ID_FIELDS: frozenset[str] = frozenset({
    "document_id", "profile_id", "feature_id", "requirement_id",
    "snapshot_id", "link_id", "item_id", "condition_id",
    "education_id", "credential_id", "experience_id", "skill_id",
    "canonical_id", "source_item_id", "source_object_id",
    "canonical_position_id", "experience_skill_feature_id",
    "experience_feature_id", "supporting_task_feature_ids",
    "declared_feature_ids", "evidence_link_ids",
    "tool_source_item_ids", "tool_skill_ids",
    "source_id", "parse_result_id",
    # Domain model identity fields (profiles.py)
    "cv_id", "position_id", "user_id", "verification_snapshot_id",
})

# Reject identifiers that start with a contact / PII semantic prefix
# followed by a separator, e.g. ``phone_13800138000``, ``contact-4155552671``.
_CONTACT_PREFIX_IN_ID = re.compile(
    r"^(?:phone|mobile|tel|contact|email|wechat|qq|whatsapp|telegram|social)"
    r"[_\-.]+",
    re.IGNORECASE,
)

# Valid structured-ID formats used by the system Contracts.
#   1. namespace-prefix + colon  (cv:…, position:…, feature:…, …)
#   2. UUID / hex-dash
#   3. word-like identifier containing at least one ASCII letter and only
#      word characters, underscores, dashes, colons or dots.
_VALID_ID_FORMAT = re.compile(
    r"^(?:"
    r"[a-z][a-z0-9]*:"           # namespace prefix (cv:, position:, …)
    r"|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # UUID
    r"|"
    r"(?=.*[a-zA-Z])[\w:.-]+"     # has ≥1 letter, valid separators
    r")$"
)
_NUMERIC_EVIDENCE_SOURCE_ID = re.compile(r"^[1-9]\d{0,8}$")


def _field_name_from_path(path: str) -> str:
    """Return the leaf field name from a JSON-path string.

    >>> _field_name_from_path("$.capabilities.profiles[3].document_id")
    'document_id'
    """
    leaf = re.sub(r"\[\d+\]$", "", path)
    return leaf.rsplit(".", 1)[-1]


def validate_structured_identifier(
    path: str, value: str
) -> tuple[PrivacyViolation, ...]:
    """Validate a *value* that belongs to a curated structured-identifier field.

    This is deliberately narrow — it only applies to field names listed in
    ``_STRUCTURED_ID_FIELDS``.  It does **not** use a broad ``*_id`` suffix
    pattern or a simple "contains a letter" check.
    """
    violations: list[PrivacyViolation] = []

    # 1. Reject contact-wrapped prefixes (phone_xxx, contact-xxx, …)
    if _CONTACT_PREFIX_IN_ID.match(value):
        violations.append(
            PrivacyViolation(path, "contact-like identifier prefix is not allowed")
        )
        return tuple(violations)

    # KG Evidence lineage uses positive database IDs. Keep this exception scoped
    # to source_id and below phone-number length.
    if (
        _field_name_from_path(path) == "source_id"
        and _NUMERIC_EVIDENCE_SOURCE_ID.fullmatch(value)
    ):
        return ()

    # 2. If the value matches a known Contract ID format, accept it.
    #    Still guard against email addresses embedded inside valid-looking IDs.
    if _VALID_ID_FORMAT.match(value):
        if _EMAIL.search(value):
            violations.append(
                PrivacyViolation(path, "email-like value is not allowed")
            )
        return tuple(violations)

    # 3. Value does NOT match any valid ID format — full PII scrutiny.
    #    3a. Reject bare phone numbers (pure digit string, 10–15 digits)
    if re.fullmatch(r"\d{10,15}", value):
        violations.append(
            PrivacyViolation(path, "phone-like value is not allowed")
        )
        return tuple(violations)

    #    3b. Reject formatted phone numbers (caught by existing detector)
    if _looks_like_phone(value):
        violations.append(
            PrivacyViolation(path, "phone-like value is not allowed")
        )
        return tuple(violations)

    #    3c. Reject email addresses
    if _EMAIL.search(value):
        violations.append(
            PrivacyViolation(path, "email-like value is not allowed")
        )
        return tuple(violations)

    # 4. Not a recognized ID format — flag for review.
    violations.append(
        PrivacyViolation(
            path,
            "structured identifier does not match expected format",
        )
    )

    return tuple(violations)


@dataclass(frozen=True)
class UrlSafetyPolicy:
    """Explicit allowlist for non-personal technical documentation URLs."""

    technical_hosts: frozenset[str]
    technical_path_markers: tuple[str, ...] = (
        "/docs/", "/doc/", "/documentation/", "/reference/", "/api/", "/manual/",
    )
    repository_hosts: frozenset[str] = frozenset({"github.com", "gitlab.com"})


DEFAULT_URL_SAFETY_POLICY = UrlSafetyPolicy(
    technical_hosts=frozenset(
        {
            "docs.python.org", "kubernetes.io", "docs.kubernetes.io",
            "redis.io", "docs.redis.com", "postgresql.org", "www.postgresql.org",
            "docs.sqlalchemy.org", "pydantic.dev", "docs.pydantic.dev",
        }
    )
)


def configured_url_safety_policy() -> UrlSafetyPolicy:
    """Merge deployment-configured technical hosts into the conservative defaults."""
    configured = os.getenv("MATCHING_PII_TECHNICAL_URL_HOSTS", "")
    hosts = set(DEFAULT_URL_SAFETY_POLICY.technical_hosts)
    personal_hosts = {
        "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "t.me"
    }
    for raw in configured.split(","):
        host = raw.strip().lower().removeprefix("www.")
        if (
            re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            and host not in personal_hosts
        ):
            hosts.add(host)
    return UrlSafetyPolicy(
        technical_hosts=frozenset(hosts),
        technical_path_markers=DEFAULT_URL_SAFETY_POLICY.technical_path_markers,
        repository_hosts=DEFAULT_URL_SAFETY_POLICY.repository_hosts,
    )


@dataclass(frozen=True)
class PrivacyViolation:
    path: str
    reason: str


def _looks_like_phone(text: str) -> bool:
    uuid_spans = tuple(match.span() for match in _UUID_TOKEN.finditer(text))
    for match in (*_CHINA_PHONE.finditer(text), *_PHONE_CANDIDATE.finditer(text)):
        if any(
            uuid_start <= match.start() and match.end() <= uuid_end
            for uuid_start, uuid_end in uuid_spans
        ):
            continue
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)
        # Separators, an international prefix or parentheses are required for generic
        # numbers, preventing versions, percentages and ordinary counters from matching.
        formatted = bool(re.search(r"[+()\s-]", candidate))
        if 10 <= len(digits) <= 15 and (formatted or _CHINA_PHONE.fullmatch(candidate)):
            return True
    return False


def _url_is_personal(raw_url: str, policy: UrlSafetyPolicy) -> bool:
    normalized = raw_url if "://" in raw_url else f"https://{raw_url}"
    parsed = urlsplit(normalized.rstrip(".,;:!?)]}"))
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {
        "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "t.me"
    }:
        return True
    if host in policy.technical_hosts or any(
        host.endswith(f".{item}") for item in policy.technical_hosts
    ):
        return False
    if host in policy.repository_hosts:
        # Repository URLs are safe; profile/contact paths and bare user pages are not.
        segments = [segment for segment in path.split("/") if segment]
        return len(segments) < 2 or any(item in segments for item in ("profile", "contact"))
    return not any(marker in f"{path}/" for marker in policy.technical_path_markers)


def find_pii(
    value: object,
    path: str = "$",
    *,
    url_policy: UrlSafetyPolicy | None = None,
    technical_context_allowed: bool = False,
) -> tuple[PrivacyViolation, ...]:
    url_policy = url_policy or configured_url_safety_policy()
    violations: list[PrivacyViolation] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            item_path = f"{path}.{key}"
            if normalized_key in _FORBIDDEN_KEYS:
                violations.append(PrivacyViolation(item_path, "forbidden PII field"))
            violations.extend(
                find_pii(
                    item,
                    item_path,
                    url_policy=url_policy,
                    technical_context_allowed=technical_context_allowed,
                )
            )
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            violations.extend(
                find_pii(
                    item,
                    f"{path}[{index}]",
                    url_policy=url_policy,
                    technical_context_allowed=technical_context_allowed,
                )
            )
    elif isinstance(value, str):
        field_name = _field_name_from_path(path)
        if field_name in _STRUCTURED_ID_FIELDS:
            violations.extend(validate_structured_identifier(path, value))
        else:
            contact_pattern = (
                _RELAXED_LABELED_CONTACT
                if technical_context_allowed
                else _LABELED_CONTACT
            )
            rules = (
                (_EMAIL, "email-like value is not allowed"),
                (_CHINA_ID, "identity-document-like value is not allowed"),
                (contact_pattern, "personal contact marker is not allowed"),
                (_LABELED_NAME, "person-name marker is not allowed"),
                (_LABELED_ID, "identity-document marker is not allowed"),
                (_LABELED_ADDRESS, "address marker is not allowed"),
                (_CHINA_ADDRESS, "address-like value is not allowed"),
                (_STREET_ADDRESS, "street-address-like value is not allowed"),
                (_HANDLE, "social handle is not allowed"),
            )
            for pattern, reason in rules:
                if pattern.search(value):
                    violations.append(PrivacyViolation(path, reason))
            if _looks_like_phone(value):
                violations.append(PrivacyViolation(path, "phone-like value is not allowed"))
            for match in _URL.finditer(value):
                if _url_is_personal(match.group(0), url_policy):
                    violations.append(PrivacyViolation(path, "personal URL is not allowed"))
    return tuple(violations)


def redact_pii(value: object, replacement: str = "[redacted]") -> object:
    """Redact complete unsafe leaves; never copy a matched secret into diagnostics."""
    if isinstance(value, Mapping):
        return {
            key: replacement
            if str(key).strip().lower() in _FORBIDDEN_KEYS
            else redact_pii(item, replacement)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return type(value)(redact_pii(item, replacement) for item in value)
    if isinstance(value, str) and find_pii(value):
        return replacement
    return value


# Public alias for upstream scrubbers that must replace forbidden-key values.
PII_FORBIDDEN_KEYS = _FORBIDDEN_KEYS
