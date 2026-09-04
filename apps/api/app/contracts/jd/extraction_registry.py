from collections.abc import Callable

from pydantic import BaseModel

from app.contracts.jd.errors import UnsupportedSchemaVersion
from app.contracts.jd.extraction_v2 import JDExtractionResult
from app.domain.json_types import JsonObject

CURRENT_EXTRACTION_VERSION = "v2"
_contracts: dict[str, type[BaseModel]] = {"v2": JDExtractionResult}
_read_adapters: dict[str, Callable[[JsonObject], JsonObject]] = {}


def register_extraction_contract(
    version: str,
    contract: type[BaseModel],
    *,
    read_adapter: Callable[[JsonObject], JsonObject] | None = None,
) -> None:
    _contracts[version] = contract
    if read_adapter:
        _read_adapters[version] = read_adapter


def get_extraction_contract(version: str = CURRENT_EXTRACTION_VERSION) -> type[BaseModel]:
    try:
        return _contracts[version]
    except KeyError as exc:
        raise UnsupportedSchemaVersion("extraction", version) from exc


def validate_extraction(payload: JsonObject, version: str | None = None) -> BaseModel:
    resolved_version = version or payload.get("schema_version") or "v2"
    if not isinstance(resolved_version, str):
        raise ValueError("schema_version must be a string")
    adapted = dict(payload)
    adapted.setdefault("schema_version", resolved_version)
    if resolved_version in _read_adapters:
        adapted = _read_adapters[resolved_version](adapted)
    return get_extraction_contract(resolved_version).model_validate(adapted)
