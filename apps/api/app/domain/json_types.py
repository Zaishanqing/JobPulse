from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None


class InvalidJsonValue(ValueError):
    """A value cannot be represented by the application's JSON contract."""


class FrozenJsonArray(tuple["FrozenJsonValue", ...]):
    """Recursively immutable JSON array."""

    def __new__(
        cls, values: Iterable["JsonInputValue"] = ()
    ) -> "FrozenJsonArray":
        return super().__new__(
            cls,
            (
                freeze_json(item, field=f"array[{index}]")
                for index, item in enumerate(values)
            ),
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return len(self) == len(other) and all(
                left == right for left, right in zip(self, other)
            )
        return NotImplemented

    __hash__ = tuple.__hash__


class FrozenJsonObject(Mapping[str, "FrozenJsonValue"]):
    """Recursively immutable JSON object with value-based equality."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, "JsonInputValue"]) -> None:
        if not all(isinstance(key, str) for key in values):
            raise InvalidJsonValue("JSON object must use string keys")
        object.__setattr__(self, "_values", MappingProxyType({
            key: freeze_json(item, field=f"object.{key}")
            for key, item in values.items()
        }))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __copy__(self) -> "FrozenJsonObject":
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> "FrozenJsonObject":
        return self

    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self._values!r})"


FrozenJsonValue: TypeAlias = JsonScalar | FrozenJsonArray | FrozenJsonObject
JsonInputValue: TypeAlias = (
    JsonScalar
    | list["JsonInputValue"]
    | Mapping[str, "JsonInputValue"]
    | FrozenJsonArray
    | FrozenJsonObject
)
JsonValue: TypeAlias = FrozenJsonValue
JsonObject: TypeAlias = FrozenJsonObject
MutableJsonValue: TypeAlias = JsonScalar | list["MutableJsonValue"] | dict[str, "MutableJsonValue"]
MutableJsonObject: TypeAlias = dict[str, MutableJsonValue]


def freeze_json(value: object, *, field: str = "value") -> FrozenJsonValue:
    """Validate JSON strictly and return a recursively immutable defensive copy."""
    if isinstance(value, float) and not isfinite(value):
        raise InvalidJsonValue(f"{field} contains a non-finite JSON number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, FrozenJsonArray)):
        return FrozenJsonArray(
            freeze_json(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise InvalidJsonValue(f"{field} must use string JSON keys")
        return FrozenJsonObject(
            {
                key: freeze_json(item, field=f"{field}.{key}")
                for key, item in value.items()
            }
        )
    raise InvalidJsonValue(
        f"{field} contains a non-JSON value of type {type(value).__name__}"
    )


def freeze_json_object(value: object, *, field: str = "value") -> FrozenJsonObject:
    frozen = freeze_json(value, field=field)
    if not isinstance(frozen, FrozenJsonObject):
        raise InvalidJsonValue(f"{field} must be a JSON object")
    return frozen


def empty_json_object() -> FrozenJsonObject:
    """Return a validated immutable empty JSON object for dataclass defaults."""
    return FrozenJsonObject({})


def thaw_json(value: FrozenJsonValue) -> MutableJsonValue:
    """Return a standard mutable JSON tree for serialization boundaries."""
    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, FrozenJsonArray):
        return [thaw_json(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        raise InvalidJsonValue("Frozen JSON contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Expected frozen JSON, got {type(value).__name__}")


def thaw_json_object(value: FrozenJsonObject) -> MutableJsonObject:
    if not isinstance(value, FrozenJsonObject):
        raise TypeError(f"Expected FrozenJsonObject, got {type(value).__name__}")
    return {key: thaw_json(item) for key, item in value.items()}
