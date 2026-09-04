from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TypeAlias, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class FrozenDict(Mapping[K, V]):
    __slots__ = ("_data",)

    def __init__(self, values: Mapping[K, V] | None = None) -> None:
        self._data = dict(values or {})

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | FrozenDict[str, "JsonValue"]
JsonObject: TypeAlias = FrozenDict[str, JsonValue]
MutableJsonValue: TypeAlias = JsonScalar | list["MutableJsonValue"] | dict[str, "MutableJsonValue"]


def freeze(value: MutableJsonValue) -> JsonValue:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: JsonValue) -> MutableJsonValue:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value
