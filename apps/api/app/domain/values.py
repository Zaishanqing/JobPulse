from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class FrozenDict(Mapping[K, V]):
    """Small immutable mapping used by domain facts and value objects."""

    __slots__ = ("_data", "_hash")

    def __init__(self, values: Mapping[K, V] | None = None) -> None:
        self._data = dict(values or {})
        self._hash: int | None = None

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(tuple(sorted((key, _hashable(value)) for key, value in self._data.items())))
        return self._hash


def freeze(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [thaw(item) for item in value]
    return value


def _hashable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_hashable(item) for item in value)
    return value
