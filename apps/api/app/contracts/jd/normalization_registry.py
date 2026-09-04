from pydantic import BaseModel

from app.contracts.jd.errors import UnsupportedSchemaVersion
from app.contracts.jd.normalization_v2 import JDNormalizedResult
from app.domain.json_types import JsonObject

CURRENT_NORMALIZATION_VERSION = "v2"
_contracts: dict[str, type[BaseModel]] = {"v2": JDNormalizedResult}


def register_normalization_contract(version: str, contract: type[BaseModel]) -> None:
    _contracts[version] = contract


def get_normalization_contract(
    version: str = CURRENT_NORMALIZATION_VERSION,
) -> type[BaseModel]:
    try:
        return _contracts[version]
    except KeyError as exc:
        raise UnsupportedSchemaVersion("normalization", version) from exc


def validate_normalization(payload: JsonObject, version: str | None = None) -> BaseModel:
    resolved_version = version or payload.get("schema_version") or "v2"
    if not isinstance(resolved_version, str):
        raise ValueError("schema_version must be a string")
    adapted = dict(payload)
    adapted.setdefault("schema_version", resolved_version)
    return get_normalization_contract(resolved_version).model_validate(adapted)
