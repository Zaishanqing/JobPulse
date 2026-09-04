from collections.abc import Mapping, Sequence


SENSITIVE_KEY_PARTS = frozenset({"secret", "password", "token", "credential", "api_key", "apikey"})


class SystemConfigRuleViolation(ValueError):
    pass


def reject_sensitive_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                raise SystemConfigRuleViolation(
                    "Credentials and secrets must be configured through environment variables"
                )
            reject_sensitive_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for item in value:
            reject_sensitive_values(item)
