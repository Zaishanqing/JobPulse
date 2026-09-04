"""Named JSON boundary values used for persisted snapshots and extensions."""

from __future__ import annotations

from collections.abc import Mapping

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | Mapping[str, "JsonValue"]
SerializedPayload = Mapping[str, JsonValue]
ExtensionAttributes = Mapping[str, JsonValue]
AuditSnapshot = Mapping[str, JsonValue]
