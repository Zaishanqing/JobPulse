from __future__ import annotations

import inspect
import ast
import copy
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace, TracebackType, UnionType
from typing import (
    Any,
    ForwardRef,
    Literal,
    Protocol,
    TypeAlias,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import pytest

from app.application.jd import JDFileCreateCommand, JDUseCases
from app.application.jd_common import JDApplicationError
from app.application.tasks import ManageTasks, build_succeeded_task
from app.domain.accounts import AccountActor
from app.ports.tasks import TaskLog, TaskPayload, TaskRecord
from app.infrastructure.jd_repository import (
    AdapterFileTextExtractor,
    JDDataMappingError,
    SqlAlchemyFileRepository,
    SqlAlchemyJDRepository,
    SqlAlchemyJDUoW,
    SqlAlchemyTaskRepository,
    _jd_create_values,
    _json_object,
    _parse_create_values,
    _parse_update_values,
    _parse_dto,
    _task_dto,
)
from app.infrastructure.extraction_tasks import RuleBasedJDExtractionProvider
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure import jd_export_serialization
from app.domain import json_types
from app.domain import tasks as task_domain
from app.domain.jd import (
    CandidateRequirement,
    Document,
    Evidence,
    Fact,
    NormalizationResult,
    NormalizedItem,
    Responsibility,
    ReviewFlag,
)
from app.domain.jd_skill_catalog import CatalogIdentity
from app.domain.jd_policies import JDParseEditCommand, JDPolicyViolation
from app.domain.json_types import (
    FrozenJsonArray,
    FrozenJsonObject,
    InvalidJsonValue,
    freeze_json_object,
    thaw_json_object,
)
from app.application.jd_support import (
    PortContractError,
    require_port_list,
)
from app.contexts.jd_lifecycle._ports import jd_repository as ports
from app.schemas import task as task_schema


DYNAMIC_JSON_FIELDS = {
    "extraction_result",
    "normalized_result",
    "extraction_payload",
    "normalization_payload",
    "input_payload",
    "result_payload",
    "raw_payload",
}
EXCLUDED_PROTOCOL_METHODS = {"__init__", "__subclasshook__"}
PORT_GLOBALS = vars(ports)
TYPE_GLOBALS = {
    **PORT_GLOBALS,
    **vars(json_types),
    **vars(jd_export_serialization),
    "Literal": Literal,
    "Mapping": Mapping,
    "Sequence": Sequence,
}


def _nested_types(annotation: object, seen: set[int] | None = None):
    seen = seen or set()
    marker = id(annotation)
    if marker in seen:
        return
    seen.add(marker)
    yield annotation
    if isinstance(annotation, ForwardRef):
        name = annotation.__forward_arg__
        resolved = TYPE_GLOBALS.get(name)
        if resolved is None:
            try:
                resolved = eval(name, TYPE_GLOBALS, TYPE_GLOBALS)
            except (NameError, SyntaxError) as exc:
                raise AssertionError(f"Unresolved ForwardRef: {name}") from exc
        yield from _nested_types(resolved, seen)
        return
    for argument in get_args(annotation):
        yield from _nested_types(argument, seen)


def _assert_no_any_or_object(annotation: object, location: str) -> None:
    forbidden = [
        item
        for item in _nested_types(annotation)
        if item is Any or item is object
    ]
    assert forbidden == [], location


def _assert_no_dictionary(annotation: object, location: str) -> None:
    dictionaries = [
        item
        for item in _nested_types(annotation)
        if item in {dict, Mapping} or get_origin(item) in {dict, Mapping}
    ]
    assert dictionaries == [], location


def _assert_typed_json(annotation: object, location: str) -> None:
    candidates = [item for item in get_args(annotation) if item is not type(None)]
    root = candidates[0] if len(candidates) == 1 else annotation
    assert root is FrozenJsonObject, (
        f"{location} must use the immutable FrozenJsonObject authority; got {root!r}"
    )
    _assert_json_value_type(root, location)
    _assert_json_value_type(json_types.FrozenJsonValue, location)


def _assert_json_value_type(annotation: object, location: str) -> None:
    if isinstance(annotation, ForwardRef):
        name = annotation.__forward_arg__
        try:
            annotation = eval(name, TYPE_GLOBALS, TYPE_GLOBALS)
        except (NameError, SyntaxError) as exc:
            raise AssertionError(f"{location} has unresolved ForwardRef {name}") from exc
    if annotation in {str, int, float, bool, type(None)}:
        return
    if annotation is FrozenJsonArray:
        bases = FrozenJsonArray.__orig_bases__
        assert len(bases) == 1 and get_origin(bases[0]) is tuple, location
        element_type, ellipsis = get_args(bases[0])
        assert ellipsis is Ellipsis, location
        _assert_json_authority_reference(element_type, location)
        return
    if annotation is FrozenJsonObject:
        bases = FrozenJsonObject.__orig_bases__
        assert len(bases) == 1 and get_origin(bases[0]) is Mapping, location
        key_type, value_type = get_args(bases[0])
        assert key_type is str, f"{location} JSON object keys must be str"
        _assert_json_authority_reference(value_type, location)
        return
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {dict, Mapping}:
        assert len(arguments) == 2 and arguments[0] is str, (
            f"{location} JSON object keys must be str"
        )
        _assert_json_value_type(arguments[1], location)
        return
    if origin is list:
        assert len(arguments) == 1, f"{location} has invalid JSON array type"
        _assert_json_value_type(arguments[0], location)
        return
    if origin is tuple:
        raise AssertionError(f"{location} contains a business tuple, not FrozenJsonArray")
    if arguments and origin in {Union, UnionType}:
        for argument in arguments:
            _assert_json_value_type(argument, location)
        return
    raise AssertionError(f"{location} contains non-JSON type {annotation!r}")


def _assert_json_authority_reference(annotation: object, location: str) -> None:
    if isinstance(annotation, ForwardRef):
        annotation = annotation.__forward_arg__
    assert annotation in {"FrozenJsonValue", json_types.FrozenJsonValue}, (
        f"{location} must recursively reference FrozenJsonValue; got {annotation!r}"
    )


def _resolve_annotation(annotation: object) -> object:
    if isinstance(annotation, ForwardRef):
        name = annotation.__forward_arg__
        try:
            return eval(name, TYPE_GLOBALS, TYPE_GLOBALS)
        except (NameError, SyntaxError) as exc:
            raise AssertionError(f"Unresolved ForwardRef: {name}") from exc
    return annotation


def _is_annotation_subtype(candidate: object, expected: object) -> bool:
    candidate = _resolve_annotation(candidate)
    expected = _resolve_annotation(expected)
    if candidate == expected or expected is Any:
        return True
    if candidate is Any:
        return False
    if expected is object:
        return True
    candidate_origin = get_origin(candidate)
    expected_origin = get_origin(expected)
    candidate_args = get_args(candidate)
    expected_args = get_args(expected)

    if candidate_origin in {Union, UnionType}:
        return all(_is_annotation_subtype(item, expected) for item in candidate_args)
    if expected_origin in {Union, UnionType}:
        return any(_is_annotation_subtype(candidate, item) for item in expected_args)

    if candidate_origin is Literal:
        if expected_origin is Literal:
            return all(
                any(
                    type(candidate_value) is type(expected_value)
                    and candidate_value == expected_value
                    for expected_value in expected_args
                )
                for candidate_value in candidate_args
            )
        return all(_is_annotation_subtype(type(value), expected) for value in candidate_args)
    if expected_origin is Literal:
        return False

    numeric_scalars = {bool, int, float}
    if candidate in numeric_scalars or expected in numeric_scalars:
        return False

    if candidate_origin is None and expected_origin is None:
        try:
            return inspect.isclass(candidate) and inspect.isclass(expected) and issubclass(
                candidate, expected
            )
        except TypeError:
            return False

    if candidate_origin is None or expected_origin is None:
        return False
    try:
        origin_compatible = issubclass(candidate_origin, expected_origin)
    except TypeError:
        origin_compatible = candidate_origin == expected_origin
    if not origin_compatible or len(candidate_args) != len(expected_args):
        return False

    if expected_origin is Mapping:
        candidate_key, candidate_value = candidate_args
        expected_key, expected_value = expected_args
        return (
            _is_annotation_subtype(candidate_key, expected_key)
            and _is_annotation_subtype(expected_key, candidate_key)
            and _is_annotation_subtype(candidate_value, expected_value)
        )
    if expected_origin is Sequence:
        return len(candidate_args) == 1 and _is_annotation_subtype(
            candidate_args[0], expected_args[0]
        )
    if expected_origin is list:
        if candidate_origin is not list:
            return False
        return all(
            _is_annotation_subtype(left, right)
            and _is_annotation_subtype(right, left)
            for left, right in zip(candidate_args, expected_args)
        )
    if expected_origin is tuple:
        if expected_args[-1:] == (Ellipsis,):
            return candidate_args[-1:] == (Ellipsis,) and _is_annotation_subtype(
                candidate_args[0], expected_args[0]
            )
        return all(
            _is_annotation_subtype(left, right)
            for left, right in zip(candidate_args, expected_args)
        )
    if expected_origin is type:
        return _is_annotation_subtype(candidate_args[0], expected_args[0])
    if expected_origin is dict:
        if candidate_origin is not dict:
            return False
        return all(
            _is_annotation_subtype(left, right)
            and _is_annotation_subtype(right, left)
            for left, right in zip(candidate_args, expected_args)
        )
    raise AssertionError(
        f"Unsupported generic annotation compatibility: {candidate!r} -> {expected!r}"
    )


def _assert_signature_compatible(
    protocol_method: object,
    implementation_method: object,
    *,
    protocol_owner: type | None = None,
    implementation_owner: type | None = None,
) -> None:
    protocol_signature = inspect.signature(protocol_method)
    implementation_signature = inspect.signature(implementation_method)
    protocol_hints = get_type_hints(
        protocol_method, globalns=TYPE_GLOBALS, localns=TYPE_GLOBALS
    )
    implementation_hints = get_type_hints(implementation_method)

    protocol_parameters = [
        parameter
        for parameter in protocol_signature.parameters.values()
        if parameter.name != "self"
    ]
    implementation_parameters = [
        parameter
        for parameter in implementation_signature.parameters.values()
        if parameter.name != "self"
    ]
    implementation_by_name = {
        parameter.name: parameter for parameter in implementation_parameters
    }
    implementation_positional = [
        parameter
        for parameter in implementation_parameters
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    implementation_var_positional = next(
        (
            parameter
            for parameter in implementation_parameters
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL
        ),
        None,
    )
    implementation_var_keyword = next(
        (
            parameter
            for parameter in implementation_parameters
            if parameter.kind is inspect.Parameter.VAR_KEYWORD
        ),
        None,
    )
    used_implementation_parameters: set[str] = set()
    protocol_positional_index = 0

    def assert_parameter_type(
        protocol_parameter: inspect.Parameter,
        implementation_parameter: inspect.Parameter,
    ) -> None:
        assert (
            implementation_parameter.annotation is not inspect.Parameter.empty
        ), f"missing implementation annotation {implementation_parameter.name}"
        assert _is_annotation_subtype(
            protocol_hints[protocol_parameter.name],
            implementation_hints[implementation_parameter.name],
        ), f"incompatible parameter {protocol_parameter.name}"

    def assert_omission_safe(
        protocol_parameter: inspect.Parameter,
        implementation_parameter: inspect.Parameter,
    ) -> None:
        if protocol_parameter.default is not inspect.Parameter.empty:
            assert implementation_parameter.default is not inspect.Parameter.empty, (
                f"implementation requires optional protocol parameter "
                f"{protocol_parameter.name}"
            )

    for protocol_parameter in protocol_parameters:
        if protocol_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            if protocol_positional_index < len(implementation_positional):
                receiver = implementation_positional[protocol_positional_index]
                used_implementation_parameters.add(receiver.name)
                assert_parameter_type(protocol_parameter, receiver)
                assert_omission_safe(protocol_parameter, receiver)
            else:
                assert implementation_var_positional is not None, (
                    f"implementation cannot receive positional parameter "
                    f"{protocol_parameter.name}"
                )
                assert_parameter_type(protocol_parameter, implementation_var_positional)
            protocol_positional_index += 1
            continue

        if protocol_parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            receiver = implementation_by_name.get(protocol_parameter.name)
            if receiver is not None:
                assert receiver.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
                    f"implementation changes call shape for {protocol_parameter.name}"
                )
                assert (
                    protocol_positional_index < len(implementation_positional)
                    and implementation_positional[protocol_positional_index] is receiver
                ), f"implementation changes positional order for {protocol_parameter.name}"
                used_implementation_parameters.add(receiver.name)
                assert_parameter_type(protocol_parameter, receiver)
                assert_omission_safe(protocol_parameter, receiver)
            else:
                assert (
                    implementation_var_positional is not None
                    and implementation_var_keyword is not None
                    and protocol_positional_index >= len(implementation_positional)
                ), (
                    f"implementation cannot receive both positional and keyword calls "
                    f"for {protocol_parameter.name}"
                )
                assert_parameter_type(protocol_parameter, implementation_var_positional)
                assert_parameter_type(protocol_parameter, implementation_var_keyword)
            protocol_positional_index += 1
            continue

        if protocol_parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            assert implementation_var_positional is not None, (
                "implementation does not accept protocol *args"
            )
            for receiver in implementation_positional[protocol_positional_index:]:
                assert receiver.default is not inspect.Parameter.empty, (
                    f"implementation adds required parameter {receiver.name}"
                )
                used_implementation_parameters.add(receiver.name)
                assert_parameter_type(protocol_parameter, receiver)
            assert_parameter_type(protocol_parameter, implementation_var_positional)
            continue

        if protocol_parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            receiver = implementation_by_name.get(protocol_parameter.name)
            if receiver is not None:
                assert receiver.kind in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }, f"implementation cannot receive keyword {protocol_parameter.name}"
                used_implementation_parameters.add(receiver.name)
                assert_parameter_type(protocol_parameter, receiver)
                assert_omission_safe(protocol_parameter, receiver)
            else:
                assert implementation_var_keyword is not None, (
                    f"implementation cannot receive keyword {protocol_parameter.name}"
                )
                assert_parameter_type(protocol_parameter, implementation_var_keyword)
            continue

        if protocol_parameter.kind is inspect.Parameter.VAR_KEYWORD:
            assert implementation_var_keyword is not None, (
                "implementation does not accept protocol **kwargs"
            )
            for receiver in implementation_parameters:
                if receiver.kind not in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }:
                    continue
                if receiver.name in used_implementation_parameters:
                    assert any(
                        parameter.name == receiver.name
                        and parameter.kind
                        in {
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            inspect.Parameter.KEYWORD_ONLY,
                        }
                        for parameter in protocol_parameters
                    ), f"protocol **kwargs can collide with {receiver.name}"
                    continue
                assert receiver.default is not inspect.Parameter.empty, (
                    f"implementation adds required parameter {receiver.name}"
                )
                used_implementation_parameters.add(receiver.name)
                assert_parameter_type(protocol_parameter, receiver)
            assert_parameter_type(protocol_parameter, implementation_var_keyword)

    for implementation_parameter in implementation_parameters:
        if implementation_parameter.name in used_implementation_parameters:
            continue
        if implementation_parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        assert implementation_parameter.default is not inspect.Parameter.empty, (
            f"implementation adds required parameter {implementation_parameter.name}"
        )

    assert implementation_signature.return_annotation is not inspect.Signature.empty
    expected_return = protocol_hints["return"]
    actual_return = implementation_hints["return"]
    if (
        protocol_owner is not None
        and implementation_owner is not None
        and expected_return is protocol_owner
        and actual_return is implementation_owner
    ):
        return
    assert _is_annotation_subtype(actual_return, expected_return), "incompatible return"


def _protocol_methods(protocol: type) -> dict[str, object]:
    methods: dict[str, object] = {}
    for base in reversed(protocol.__mro__):
        if base is object or base.__module__ in {"typing", "abc"}:
            continue
        for name, value in base.__dict__.items():
            if (
                name not in EXCLUDED_PROTOCOL_METHODS
                and inspect.isfunction(value)
                and value.__module__ != "typing"
            ):
                methods[name] = value
    return methods


def _public_port_dataclasses():
    return tuple(
        value
        for name, value in PORT_GLOBALS.items()
        if not name.startswith("_")
        and inspect.isclass(value)
        and value.__module__ == ports.__name__
        and is_dataclass(value)
    )


def _public_port_protocols():
    return tuple(
        value
        for name, value in PORT_GLOBALS.items()
        if not name.startswith("_")
        and inspect.isclass(value)
        and value.__module__ == ports.__name__
        and getattr(value, "_is_protocol", False)
    )


def _port_type_aliases():
    aliases = []
    for name, marker in ports.__annotations__.items():
        if marker is TypeAlias or marker == "TypeAlias":
            aliases.append((name, getattr(ports, name)))
    return tuple(aliases)


def test_jd_port_protocols_and_dtos_have_recursive_typed_contracts():
    dataclasses = _public_port_dataclasses()
    protocols = _public_port_protocols()
    assert len(dataclasses) >= 34
    assert {
        "JDExportPort", "JDSchemaPort", "JDRepository", "FileRepository",
        "FileTextExtractor", "TaskRepository", "JDUoW",
    } <= {item.__name__ for item in protocols}

    for dto in dataclasses:
        for field_name, annotation in get_type_hints(
            dto, globalns=PORT_GLOBALS, localns=PORT_GLOBALS
        ).items():
            location = f"{dto.__name__}.{field_name}"
            _assert_no_any_or_object(annotation, location)
            if field_name in DYNAMIC_JSON_FIELDS:
                _assert_typed_json(annotation, location)
            else:
                _assert_no_dictionary(annotation, location)

    contract_method_count = 0
    for protocol in protocols:
        for method_name, method in _protocol_methods(protocol).items():
            contract_method_count += 1
            signature = inspect.signature(method)
            assert all(
                parameter.kind is not inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            ), f"{protocol.__name__}.{method_name} has **kwargs"
            for parameter_name, parameter in signature.parameters.items():
                if parameter_name != "self":
                    assert parameter.annotation is not inspect.Parameter.empty, (
                        f"{protocol.__name__}.{method_name}.{parameter_name}"
                    )
            assert signature.return_annotation is not inspect.Signature.empty
            for parameter_name, annotation in get_type_hints(
                method, globalns=PORT_GLOBALS, localns=PORT_GLOBALS
            ).items():
                location = f"{protocol.__name__}.{method_name}.{parameter_name}"
                _assert_no_any_or_object(annotation, location)
                if parameter_name in DYNAMIC_JSON_FIELDS:
                    _assert_typed_json(annotation, location)
                else:
                    _assert_no_dictionary(annotation, location)
    assert contract_method_count >= 29

    aliases = _port_type_aliases()
    assert {
        "FileExtractionStatus", "JDUpdateCommand",
        "JDParseResultUpdateCommand",
    } <= {name for name, _ in aliases}
    assert ports.TaskStatus is task_domain.TaskStatus
    for alias_name, annotation in aliases:
        _assert_no_any_or_object(annotation, alias_name)
        if alias_name not in {"JsonObject", "JsonValue"}:
            _assert_no_dictionary(annotation, alias_name)


@pytest.mark.parametrize(
    ("protocol", "implementation"),
    [
        (ports.JDExportPort, OpenPyxlJDExporter),
        (ports.JDSchemaPort, VersionedJDSchemaAdapter),
        (ports.JDRepository, SqlAlchemyJDRepository),
        (ports.FileRepository, SqlAlchemyFileRepository),
        (ports.FileTextExtractor, AdapterFileTextExtractor),
        (ports.TaskRepository, SqlAlchemyTaskRepository),
        (ports.JDUoW, SqlAlchemyJDUoW),
    ],
)
def test_port_implementations_use_explicit_compatible_signatures(protocol, implementation):
    for method_name, method in _protocol_methods(protocol).items():
        implementation_method = getattr(implementation, method_name)
        _assert_signature_compatible(
            method,
            implementation_method,
            protocol_owner=protocol,
            implementation_owner=implementation,
        )
        for annotation in get_type_hints(implementation_method).values():
            _assert_no_any_or_object(
                annotation, f"{implementation.__name__}.{method_name}"
            )


def test_jd_excel_serialization_has_explicit_value_types():
    for function_name in (
        "_evidence_columns", "_excel_cell", "to_export_rows", "export_workbook",
        "serialize_export",
    ):
        function = getattr(jd_export_serialization, function_name)
        signature = inspect.signature(function)
        assert all(
            parameter.annotation is not inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )
        assert signature.return_annotation is not inspect.Signature.empty
        for annotation in get_type_hints(function).values():
            _assert_no_any_or_object(annotation, function_name)


def test_recursive_json_type_helper_accepts_authority_and_rejects_false_types():
    assert set(get_args(json_types.JsonScalar)) == {
        str, int, float, bool, type(None)
    }
    assert json_types.JsonObject is FrozenJsonObject
    assert json_types.JsonValue == json_types.FrozenJsonValue
    _assert_typed_json(FrozenJsonObject, "valid")
    _assert_json_value_type(json_types.FrozenJsonValue, "FrozenJsonValue")
    for invalid in (
        dict[int, str], dict[str, bytes], dict[str, tuple[int, ...]],
        dict[str, set[str]], dict[str, object], dict[str, bytearray],
        dict[str, memoryview], dict[str, frozenset[str]], dict[str, complex],
    ):
        with pytest.raises(AssertionError):
            _assert_json_value_type(invalid, "invalid")
    with pytest.raises(AssertionError, match="unresolved ForwardRef"):
        _assert_json_value_type(ForwardRef("UnknownJsonType"), "invalid")


def test_frozen_json_public_behavior_is_immutable_and_copy_safe():
    source = {"items": [{"name": "Python"}]}
    frozen = freeze_json_object(source)
    source["items"][0]["name"] = "mutated"
    assert frozen["items"][0]["name"] == "Python"
    for mutation in (
        lambda: setattr(frozen, "_values", {"bad": {1}}),
        lambda: setattr(frozen, "new_attribute", 1),
        lambda: delattr(frozen, "_values"),
    ):
        with pytest.raises(AttributeError, match="immutable"):
            mutation()
    assert copy.copy(frozen) is frozen
    assert copy.deepcopy(frozen) is frozen
    first = thaw_json_object(frozen)
    second = thaw_json_object(frozen)
    first["items"][0]["name"] = "first-only"
    assert second["items"][0]["name"] == "Python"
    assert frozen["items"][0]["name"] == "Python"


def test_jd_domain_json_defaults_are_frozen():
    evidence = Evidence("source", "quote", 0, 5, "exact", 0)
    values = (
        Responsibility("r", "must", evidence).raw_payload,
        CandidateRequirement("q", "skill", "must", evidence).raw_payload,
        Fact("f", "job", "title", "Engineer", evidence).raw_payload,
        Document("jd", "v2").payload,
        NormalizedItem("Python", "skill", "resolved").raw_payload,
        ReviewFlag("unknown", "x", "reason").raw_payload,
    )
    assert all(isinstance(value, FrozenJsonObject) for value in values)
    normalization = NormalizationResult("jd", "v2")
    assert normalization.job_classification is None
    assert normalization.salary is None


@pytest.mark.parametrize(
    ("factory", "attribute"),
    [
        (lambda payload, e: Responsibility("r", "must", e, payload), "raw_payload"),
        (
            lambda payload, e: CandidateRequirement(
                "q", "skill", "must", e, payload
            ),
            "raw_payload",
        ),
        (lambda payload, e: Fact("f", "job", "title", "x", e, payload), "raw_payload"),
        (lambda payload, e: Document("jd", "v2", payload=payload), "payload"),
        (
            lambda payload, e: NormalizedItem(
                "Python", "skill", "resolved", raw_payload=payload
            ),
            "raw_payload",
        ),
        (
            lambda payload, e: ReviewFlag(
                "unknown", "x", "reason", raw_payload=payload
            ),
            "raw_payload",
        ),
        (
            lambda payload, e: NormalizationResult(
                "jd", "v2", job_classification=payload
            ),
            "job_classification",
        ),
        (
            lambda payload, e: NormalizationResult("jd", "v2", salary=payload),
            "salary",
        ),
    ],
)
def test_jd_domain_json_inputs_are_defensively_frozen_and_strict(factory, attribute):
    evidence = Evidence("source", "quote", 0, 5, "exact", 0)
    source = {"nested": [{"value": 1}]}
    domain_value = factory(source, evidence)
    frozen = getattr(domain_value, attribute)
    assert isinstance(frozen, FrozenJsonObject)
    source["nested"][0]["value"] = 2
    assert frozen["nested"][0]["value"] == 1
    with pytest.raises(TypeError):
        frozen["bad"] = 1
    with pytest.raises(InvalidJsonValue):
        factory({"bad": {1}}, evidence)
    assert copy.deepcopy(domain_value) == domain_value


def test_signature_compatibility_supports_variance_and_rejects_invalid_shapes():
    def value_protocol(value: str) -> object: ...
    def wider_value(value: object) -> str: return str(value)
    def extra_kwargs(value: object, **kwargs: object) -> str: return str(value)
    def extra_args(value: object, *args: object) -> str: return str(value)
    def optional_keyword(
        value: object, *, debug: bool = False
    ) -> str: return str(value)
    def keyword_protocol(value: str, *, mode: str) -> object: ...
    def keyword_via_kwargs(value: object, **kwargs: object) -> str: return str(value)
    def args_protocol(*values: str) -> object: ...
    def wider_args(*values: object) -> str: return str(values)
    def kwargs_protocol(**options: str) -> object: ...
    def wider_kwargs(**options: object) -> str: return str(options)
    def positional_protocol(value: str, /) -> object: ...
    def positional_implementation(value: object, /) -> str: return str(value)
    def generic_protocol(values: list[str]) -> Sequence[object]: ...
    def generic_implementation(values: Sequence[object]) -> list[str]:
        return [str(value) for value in values]

    def narrow_parameter(value: int) -> object: return value
    def bool_protocol(value: bool) -> object: ...
    def int_parameter(value: int) -> object: return value
    def int_return_protocol() -> int: ...
    def bool_return() -> bool: return True
    def extra_required(value: object, required: int) -> str: return str(value)
    def keyword_only_value(*, value: object) -> str: return str(value)
    def keyword_only_protocol(*, value: str) -> object: ...
    def positional_only_value(value: object, /) -> str: return str(value)
    def no_args() -> str: return ""
    def no_kwargs() -> str: return ""
    def narrow_optional_then_args(
        prefix: int = 0, *values: object
    ) -> str: return str((prefix, values))
    def narrow_optional_then_kwargs(
        mode: int = 0, **options: object
    ) -> str: return str((mode, options))
    def missing_mode(value: object) -> str: return str(value)
    def str_return_protocol() -> str: ...
    def optional_str_return() -> str | None: return None
    def optional_protocol(value: str = "default") -> object: ...
    def now_required(value: object) -> str: return str(value)

    for protocol_method, implementation_method in (
        (value_protocol, wider_value),
        (value_protocol, extra_kwargs),
        (value_protocol, extra_args),
        (value_protocol, optional_keyword),
        (keyword_protocol, keyword_via_kwargs),
        (args_protocol, wider_args),
        (kwargs_protocol, wider_kwargs),
        (positional_protocol, positional_implementation),
        (generic_protocol, generic_implementation),
    ):
        _assert_signature_compatible(protocol_method, implementation_method)

    for protocol_method, implementation_method in (
        (value_protocol, narrow_parameter),
        (bool_protocol, int_parameter),
        (int_return_protocol, bool_return),
        (value_protocol, extra_required),
        (value_protocol, keyword_only_value),
        (keyword_only_protocol, positional_only_value),
        (args_protocol, no_args),
        (kwargs_protocol, no_kwargs),
        (args_protocol, narrow_optional_then_args),
        (kwargs_protocol, narrow_optional_then_kwargs),
        (keyword_protocol, missing_mode),
        (str_return_protocol, optional_str_return),
        (optional_protocol, now_required),
    ):
        with pytest.raises(AssertionError):
            _assert_signature_compatible(protocol_method, implementation_method)

    assert _is_annotation_subtype(str, object)
    assert _is_annotation_subtype(str, str | int)
    assert _is_annotation_subtype(str | int, object)
    assert _is_annotation_subtype(Literal["a"], Literal["a", "b"])
    assert _is_annotation_subtype(Literal[True], bool)
    assert _is_annotation_subtype(Literal[1], int)
    assert not _is_annotation_subtype(Literal[True], int)
    assert not _is_annotation_subtype(Literal[1], bool)
    assert not _is_annotation_subtype(Literal[True], Literal[1])
    assert not _is_annotation_subtype(Literal[1], Literal[True])
    assert not _is_annotation_subtype(str, Literal["a"])
    assert _is_annotation_subtype(bool, bool)
    assert not _is_annotation_subtype(bool, int)
    assert not _is_annotation_subtype(bool, float)
    assert not _is_annotation_subtype(int, bool)
    assert not _is_annotation_subtype(float, bool)
    assert _is_annotation_subtype(list[str], Sequence[object])
    assert _is_annotation_subtype(dict[str, str], Mapping[str, object])
    assert not _is_annotation_subtype(list[str], list[object])
    assert not _is_annotation_subtype(int, str)
    assert not _is_annotation_subtype(object | None, str)
    with pytest.raises(AssertionError, match="Unsupported generic annotation"):
        _is_annotation_subtype(set[str], set[object])


def test_protocol_method_discovery_includes_user_declared_mro_methods():
    class BaseContract(Protocol):
        def inherited(self, value: str) -> int: ...

    class ChildContract(BaseContract, Protocol):
        def direct(self) -> None: ...

    assert set(_protocol_methods(ChildContract)) == {"inherited", "direct"}


def test_excel_cells_use_stable_json_and_reject_invalid_runtime_values():
    first = jd_export_serialization._excel_cell({"a": 1, "b": [True, None, "中文"]})
    second = jd_export_serialization._excel_cell({"b": [True, None, "中文"], "a": 1})
    assert first == second == '{"a":1,"b":[true,null,"中文"]}'
    assert jd_export_serialization._excel_cell(True) is True
    assert jd_export_serialization._excel_cell(7) == 7
    assert jd_export_serialization._excel_cell(0.5) == 0.5
    for invalid in (
        float("nan"), float("inf"), float("-inf"),
        b"bytes", bytearray(b"bytes"), memoryview(b"bytes"),
        {"set"}, frozenset({"set"}), ("tuple",), complex(1, 2), object(),
        {1: "bad-key"}, {"nested": {1: "bad-key"}},
        {"nested": [("tuple",)]},
    ):
        with pytest.raises((TypeError, ValueError)):
            jd_export_serialization._excel_cell(invalid)


def test_api_task_schema_reuses_shared_task_status_authority():
    from app.main import app

    assert task_schema.TaskStatus is task_domain.TaskStatus
    openapi = app.openapi()
    components = openapi["components"]["schemas"]

    def resolve(schema):
        while "$ref" in schema:
            schema = components[schema["$ref"].rsplit("/", 1)[-1]]
        return schema

    operation = openapi["paths"]["/api/v1/jds/parse-tasks/{task_id}"]["get"]
    response_schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema != {}
    envelope_schema = resolve(response_schema)
    data_schema = resolve(envelope_schema["properties"]["data"])
    assert data_schema["title"] == "TaskRecordResponse"
    assert "TaskRecordResponse" in components
    assert data_schema["properties"]["canonical_status"]["enum"] == [
        "pending", "running", "succeeded", "failed", "cancelled"
    ]


def test_jd_infrastructure_contract_mappers_do_not_import_api_contracts():
    root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "app/infrastructure/jd_extraction_mapper.py",
        "app/infrastructure/jd_normalization_mapper.py",
        "app/infrastructure/jd_extraction_postprocessor.py",
        "app/infrastructure/jd_pipeline.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "app.api.contracts" not in source, relative_path


def test_jd_application_does_not_read_core_results_through_string_keys():
    root = Path(__file__).resolve().parents[1]
    violations = []
    for path in (root / "app" / "application").glob("jd*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                violations.append((path.name, node.lineno, "subscript"))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
            ):
                violations.append((path.name, node.lineno, "get"))
    assert violations == []


def test_all_structured_repository_writes_are_checked_at_application_boundary():
    root = Path(__file__).resolve().parents[1]
    structured_writes = {
        "create_jd", "update_jd", "create_parse_result",
        "update_parse_result", "upsert_parse_result",
    }
    unchecked = []
    for path in (root / "app" / "application").glob("jd*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in structured_writes
            ):
                continue
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id == "require_port_result"
            ):
                unchecked.append((path.name, node.lineno, node.func.attr))
    assert unchecked == []


def test_port_list_contract_rejects_outer_and_indexed_item_errors():
    with pytest.raises(
        PortContractError,
        match=r"JDRepository\.list_jds expected list\[JDDTO\], got tuple",
    ):
        require_port_list((), ports.JDDTO, operation="JDRepository.list_jds")
    with pytest.raises(
        PortContractError,
        match=r"JDRepository\.list_jds\[1\] expected JDDTO, got dict",
    ):
        require_port_list(
            [object.__new__(ports.JDDTO), {"bad": 1}],
            ports.JDDTO,
            operation="JDRepository.list_jds",
        )


def _parse_row(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": "parse-1",
        "jd_id": "jd-1",
        "position_title": "后端工程师",
        "responsibilities": ["开发服务"],
        "required_skills": [
            {
                "raw_skill": "Python",
                "normalized_skill_id": "skill_python",
                "confidence": 0.98,
                "resolution_status": "resolved",
            }
        ],
        "bonus_skills": [],
        "education": None,
        "experience": "3年",
        "industry": None,
        "tools": ["Docker"],
        "business_scenarios": ["后端服务"],
        "parse_confidence": 0.91,
        "need_review": False,
        "extraction_result": {"schema_version": "v2", "audit": {"kept": True}},
        "normalized_result": {"schema_version": "v2", "items": ["Python"]},
        "execution_metadata": None,
        "schema_version": "v2",
        "normalization_schema_version": "v2",
        "workflow_status": "reviewed",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_persistence_row_maps_to_typed_dto_and_preserves_json_and_lists():
    dto = _parse_dto(_parse_row())

    assert dto.required_skills == (
        ports.JDSkillDTO("Python", "skill_python", 0.98, "resolved"),
    )
    assert dto.bonus_skills == ()
    assert dto.education is None
    assert dto.extraction_result == {
        "schema_version": "v2",
        "audit": {"kept": True},
    }


def test_task_persistence_row_maps_fixed_logs_and_dynamic_json_separately():
    now = datetime.now(timezone.utc)
    dto = _task_dto(
        SimpleNamespace(
            id="task-1",
            task_type="jd_parse",
            status="succeeded",
            progress=1.0,
            input_payload={"jd_id": "jd-1"},
            result_payload={"audit": {"provider": "local_rules"}},
            result_reference="jd_parse_result:parse-1",
            error_code=None,
            error_message=None,
            created_by="admin-1",
            attempt_count=1,
            log_entries=[{"status": "succeeded", "at": now.isoformat()}],
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
        )
    )

    assert dto.log_entries == (ports.TaskLogDTO("succeeded", now.isoformat()),)
    assert dto.result_payload == {"audit": {"provider": "local_rules"}}


def test_typed_commands_map_to_complete_persistence_inputs():
    create = ports.JDCreateCommand(
        source_type="enterprise",
        source_name=None,
        enterprise_id="enterprise-1",
        title="后端工程师",
        raw_text="岗位职责",
        input_provider="direct_text",
    )
    assert _jd_create_values(create)["source_name"] is None
    assert _jd_create_values(create)["raw_text"] == "岗位职责"

    parse = ports.JDParseResultCreateCommand(
        jd_id="jd-1",
        legacy=ports.JDLegacyFields(
            "后端工程师",
            ("开发服务",),
            (ports.JDSkillDTO("Python", "skill_python", 0.98, "resolved"),),
            (),
            None,
            "3年",
            None,
            ("Docker",),
        ),
        business_scenarios=("后端服务",),
        parse_confidence=0.91,
        need_review=False,
        extraction_result={"schema_version": "v2", "audit": {"kept": True}},
        normalized_result={"schema_version": "v2"},
    )
    values = _parse_create_values(parse)
    assert values["required_skills"] == [
        {
            "raw_skill": "Python",
            "normalized_skill_id": "skill_python",
            "confidence": 0.98,
            "resolution_status": "resolved",
        }
    ]
    assert values["extraction_result"] == parse.extraction_result
    assert values["education"] is None


@pytest.mark.parametrize(
    "required_skills",
    [[{"confidence": 0.9}], [{"raw_skill": "Python"}], ["Python"]],
)
def test_parse_row_rejects_missing_or_invalid_core_skill_fields(required_skills):
    with pytest.raises(ValueError):
        _parse_dto(_parse_row(required_skills=required_skills))


@pytest.mark.parametrize(
    ("field", "value", "index_type"),
    [
        ("responsibilities", None, None),
        ("responsibilities", [1], "int"),
        ("tools", [True], "bool"),
        ("business_scenarios", [{}], "FrozenJsonObject"),
    ],
)
def test_parse_row_rejects_corrupt_required_collections(field, value, index_type):
    with pytest.raises(JDDataMappingError) as exc_info:
        _parse_dto(_parse_row(**{field: value}))
    assert field in str(exc_info.value)
    if index_type is not None:
        assert "[0]" in str(exc_info.value)
        assert index_type in str(exc_info.value)


def test_parse_row_preserves_legal_empty_string_collections():
    dto = _parse_dto(
        _parse_row(
            responsibilities=[], required_skills=[], bonus_skills=[], tools=[],
            business_scenarios=[],
        )
    )
    assert dto.responsibilities == ()
    assert dto.required_skills == ()
    assert dto.tools == ()
    assert dto.business_scenarios == ()


def _task_row(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": "task-1",
        "task_type": "jd_parse",
        "status": "succeeded",
        "progress": 1.0,
        "input_payload": {"jd_id": "jd-1"},
        "result_payload": {"ok": True},
        "result_reference": "jd_parse_result:parse-1",
        "error_code": None,
        "error_message": None,
        "created_by": "admin-1",
        "attempt_count": 1,
        "log_entries": [{"status": "succeeded", "at": now.isoformat()}],
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "finished_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("field", ["input_payload", "result_payload", "log_entries"])
def test_task_row_rejects_corrupt_required_json_fields(field):
    with pytest.raises(JDDataMappingError, match=field):
        _task_dto(_task_row(**{field: None}))


@pytest.mark.parametrize(
    "value",
    [
        {"bad": {1, 2}},
        {"bad": object()},
        {1: "non-string-key"},
        {"bad": b"binary"},
    ],
)
def test_json_mapper_rejects_non_json_values(value):
    with pytest.raises(JDDataMappingError):
        _json_object(value, field="payload")


def test_json_mapper_preserves_legal_nested_json():
    payload = {"items": [{"name": "Python", "active": True}], "score": 0.9}
    assert _json_object(payload, field="payload") == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"bad": {1}}, {"bad": b"bytes"}, {"bad": ("tuple",)},
        {"bad": complex(1, 2)}, {1: "non-string"},
        {"bad": float("nan")}, {"bad": float("inf")}, {"bad": float("-inf")},
    ],
)
def test_frozen_json_rejects_every_non_json_runtime_value(payload):
    with pytest.raises(InvalidJsonValue):
        freeze_json_object(payload, field="payload")


def test_parse_commands_freeze_defensively_and_persistence_thaws():
    extraction = {"items": [{"name": "Python"}]}
    normalized = {"audit": {"kept": True}}
    command = ports.JDParseResultCreateCommand(
        jd_id="jd-1",
        legacy=ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
        business_scenarios=(), parse_confidence=0.9, need_review=False,
        extraction_result=extraction, normalized_result=normalized,
    )
    extraction["items"][0]["name"] = "mutated"
    normalized["audit"]["kept"] = False
    assert command.extraction_result["items"][0]["name"] == "Python"
    assert command.normalized_result["audit"]["kept"] is True
    with pytest.raises(TypeError):
        command.extraction_result["bad"] = {1}
    with pytest.raises(TypeError):
        command.extraction_result["items"][0]["bad"] = {1}

    values = _parse_create_values(command)
    assert type(values["extraction_result"]) is dict
    assert type(values["extraction_result"]["items"]) is list
    assert values["extraction_result"] == {"items": [{"name": "Python"}]}
    values["extraction_result"]["items"][0]["name"] = "persistence-only"
    assert command.extraction_result["items"][0]["name"] == "Python"


def test_versioned_parse_update_freezes_and_thaws_json():
    source = {"document_id": "jd-1", "nested": {"score": 1.0}}
    update = ports.JDParseResultVersionedUpdate(
        legacy=ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
        extraction_result=source,
        normalized_result={"document_id": "jd-1"},
        schema_version="v2", normalization_schema_version="v2",
        need_review=True, workflow_status="draft",
    )
    source["nested"]["score"] = 0.0
    values = _parse_update_values(update)
    assert values["extraction_result"] == {
        "document_id": "jd-1", "nested": {"score": 1.0}
    }
    assert type(values["extraction_result"]) is dict


def test_parse_persistence_revalidates_even_if_a_command_is_tampered_with():
    create = ports.JDParseResultCreateCommand(
        "jd-1", ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
        (), 0.5, False, {}, {},
    )
    object.__setattr__(create, "extraction_result", {"bad": {1}})
    with pytest.raises(JDDataMappingError, match="extraction_result"):
        _parse_create_values(create)

    update = ports.JDParseResultVersionedUpdate(
        ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
        {}, {}, "v2", "v2", True, "draft",
    )
    object.__setattr__(update, "normalized_result", {"bad": float("nan")})
    with pytest.raises(JDDataMappingError, match="normalized_result"):
        _parse_update_values(update)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ports.JDParseResultCreateCommand(
            "jd-1", ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
            (), 0.5, False, {"bad": {1}}, None,
        ),
        lambda: ports.JDParseResultCreateCommand(
            "jd-1", ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
            (), 0.5, False, None, {"bad": b"bytes"},
        ),
        lambda: ports.JDParseResultVersionedUpdate(
            ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
            {1: "bad"}, {}, "v2", "v2", True, "draft",
        ),
        lambda: ports.JDParseResultVersionedUpdate(
            ports.JDLegacyFields(None, (), (), (), None, None, None, ()),
            {}, {"bad": float("nan")}, "v2", "v2", True, "draft",
        ),
    ],
)
def test_parse_commands_reject_invalid_json_at_construction(factory):
    with pytest.raises(InvalidJsonValue):
        factory()


def test_api_application_edit_command_freezes_json_and_rejects_bool_confidence():
    source = {"nested": [1]}
    command = JDParseEditCommand(
        changed_fields=frozenset({"extraction_result"}),
        extraction_result=source,
    )
    source["nested"].append(2)
    assert command.extraction_result == {"nested": [1]}
    with pytest.raises(TypeError):
        command.extraction_result["bad"] = {1}
    with pytest.raises(JDPolicyViolation):
        JDParseEditCommand(
            changed_fields=frozenset({"parse_confidence"}),
            parse_confidence=True,
        )


def test_task_payloads_are_defensively_frozen_and_thaw_to_standard_json():
    source = {"nested": [1, {"ok": True}]}
    dto = _task_dto(_task_row(input_payload=source))
    source["nested"][1]["ok"] = False
    assert dto.input_payload["nested"][1]["ok"] is True
    with pytest.raises(TypeError):
        dto.input_payload["bad"] = {1}
    with pytest.raises(TypeError):
        dto.input_payload._values["bad"] = {1}
    assert thaw_json_object(dto.input_payload) == {"nested": [1, {"ok": True}]}


def test_frozen_json_constructors_cannot_bypass_recursive_validation():
    with pytest.raises(InvalidJsonValue):
        FrozenJsonObject({"bad": {1}})
    with pytest.raises(InvalidJsonValue):
        FrozenJsonArray([{"bad": b"bytes"}])


def test_schema_json_dtos_freeze_defensively():
    source = {"nested": [1]}
    persistence = ports.JDSchemaPersistence(source, {}, "v2", "v2")
    view = ports.JDSchemaView(source, None, "available", "missing")
    source["nested"].append(2)
    assert persistence.extraction_payload == {"nested": [1]}
    assert view.extraction_result == {"nested": [1]}
    with pytest.raises(TypeError):
        persistence.extraction_payload["bad"] = {1}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ports.FileTextExtractionResult("nonsense", "", "parser"),
        lambda: ports.FileTextExtractionResult(
            "completed", "text", "parser", "Unexpected", "bad"
        ),
        lambda: ports.FileTextExtractionResult(
            "failed", "success text", "ocr", "OCRFailure", "bad"
        ),
    ],
)
def test_file_extraction_result_rejects_invalid_status_combinations(factory):
    with pytest.raises(ValueError):
        factory()


def test_file_extraction_result_accepts_legal_success_and_failure():
    assert ports.FileTextExtractionResult("completed", "text", "parser").text == "text"
    failed = ports.FileTextExtractionResult(
        "failed", "", "ocr", "OCRFailure", "cannot read"
    )
    assert failed.error_code == "OCRFailure"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ports.FileTextExtractionResult("completed", "text", "   "),
        lambda: ports.FileTextExtractionResult("completed", "   ", "parser"),
        lambda: ports.FileTextExtractionResult("failed", "", "parser", "   ", "bad"),
        lambda: ports.FileTextExtractionResult("failed", "", "parser", "E", "   "),
    ],
)
def test_file_extraction_result_rejects_blank_contract_strings(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "unknown"},
        {"progress": -0.01},
        {"progress": 1.01},
        {"progress": True},
        {"progress": float("nan")},
        {"progress": float("inf")},
        {"progress": "0.5"},
        {"attempt_count": -1},
        {"attempt_count": True},
        {"attempt_count": 1.5},
    ],
)
def test_task_dto_rejects_invalid_status_and_numeric_ranges(overrides):
    with pytest.raises(ValueError):
        _task_dto(_task_row(**overrides))


def test_task_dto_accepts_legal_succeeded_task():
    task = _task_dto(_task_row())
    assert task.status == "succeeded"
    assert task.progress == 1.0
    copied = copy.deepcopy(task)
    assert copied == task
    assert copied.input_payload is task.input_payload
    assert copied.result_payload is task.result_payload


def test_task_dto_accepts_shared_task_transition_history_without_second_policy():
    now = datetime.now(timezone.utc)
    pending = TaskRecord(
        "task-shared", "jd_parse", "pending", 0.0,
        TaskPayload.from_mapping({"jd_id": "jd-1"}), TaskPayload.from_mapping(),
        None, None, None, "admin-1", 0,
        (TaskLog("pending", now.isoformat()),), now, now, None, None,
    )

    class Repository:
        def __init__(self, record: TaskRecord) -> None:
            self.record = record

        def get(self, task_id: str) -> TaskRecord | None:
            return self.record if task_id == self.record.task_id else None

        def save(self, record: TaskRecord) -> None:
            self.record = record

    class UoW:
        def __init__(self, repository: Repository) -> None:
            self.tasks = repository

        def __enter__(self) -> "UoW":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def commit(self) -> None:
            return None

    repository = Repository(pending)
    service = ManageTasks(lambda: UoW(repository))
    actor = AccountActor("admin-1", "admin")
    running = service.transition(actor, pending.task_id, "running")
    failed = service.transition(actor, pending.task_id, "failed")
    repository.record = pending
    cancelled = service.transition(actor, pending.task_id, "cancelled")
    succeeded = build_succeeded_task(
        actor, "jd_parse", task_id="task-succeeded"
    )

    def map_shared(record: TaskRecord) -> ports.TaskDTO:
        return _task_dto(SimpleNamespace(
            id=record.task_id, task_type=record.task_type, status=record.status,
            progress=record.progress, input_payload=dict(record.input_payload),
            result_payload=dict(record.result_payload), result_reference=record.result_reference,
            error_code=record.error_code, error_message=record.error_message,
            created_by=record.created_by, attempt_count=record.attempt_count,
            log_entries=[
                {"status": item.status, "at": item.at, "message": item.message}
                for item in record.logs
            ],
            created_at=record.created_at, updated_at=record.updated_at,
            started_at=record.started_at, finished_at=record.finished_at,
        ))

    assert [
        map_shared(item).status
        for item in (pending, running, succeeded, failed, cancelled)
    ] == [
        "pending", "running", "succeeded", "failed", "cancelled"
    ]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ports.JDSkillDTO("Python", None, -0.01),
        lambda: ports.JDCopyRiskUpdate(1.01),
        lambda: ports.JDInflationUpdate(True),
    ],
)
def test_bounded_jd_numeric_fields_reject_invalid_values(factory):
    with pytest.raises(ValueError):
        factory()


def test_old_untyped_core_entries_are_rejected():
    repository = SqlAlchemyJDRepository(SimpleNamespace())
    with pytest.raises(TypeError):
        repository.create_jd({"title": "legacy"})
    with pytest.raises(TypeError):
        repository.update_jd("jd-1", {"title": "legacy"})
    with pytest.raises(TypeError):
        repository.create_parse_result({"jd_id": "jd-1"})
    with pytest.raises(TypeError):
        repository.update_parse_result("parse-1", {"need_review": False})
    with pytest.raises(TypeError):
        repository.upsert_parse_result("jd-1", {"jd_id": "jd-1"})

    extractor = AdapterFileTextExtractor(SimpleNamespace())
    with pytest.raises(TypeError):
        extractor.extract_text({"id": "legacy"}, use_ocr=False)

    schema = VersionedJDSchemaAdapter()
    with pytest.raises(TypeError):
        schema.load({}, {}, versions={"schema_version": "v2"})
    with pytest.raises(TypeError):
        schema.edit({}, None, versions={"schema_version": "v2"})
    with pytest.raises(TypeError):
        schema.view(
            None, None, schema_version="v2", normalization_schema_version="v2",
            versions={"schema_version": "v2"},
        )


class _FakeJDRepository:
    def __init__(self) -> None:
        self.command: ports.JDCreateCommand | None = None

    def create_jd(self, command: ports.JDCreateCommand) -> ports.JDDTO:
        self.command = command
        now = datetime.now(timezone.utc)
        return ports.JDDTO(
            "jd-1", command.source_type, command.source_name, command.enterprise_id,
            command.title, command.raw_text, command.publish_date, command.url,
            command.file_id, command.parse_status, command.input_extraction_status,
            command.input_provider, command.input_error_code, command.input_error_message,
            None, None, False, now, now,
        )


class _FakeFileRepository:
    def save_upload(
        self,
        actor: ports.Actor,
        upload: ports.FileUpload,
        *,
        purpose: str,
    ) -> ports.FileAssetDTO:
        return ports.FileAssetDTO(
            "file-1", actor.id, upload.filename, upload.content_type,
            "stored/file-1", len(upload.content), purpose,
        )


class _FakeFileTextExtractor:
    def __init__(self, result: ports.FileTextExtractionResult) -> None:
        self._result = result

    def extract_text(
        self, file_asset: ports.FileAssetDTO, *, use_ocr: bool
    ) -> ports.FileTextExtractionResult:
        return self._result


class _FakeUoW:
    def __init__(self, extraction: ports.FileTextExtractionResult) -> None:
        self.jds = _FakeJDRepository()
        self.files = _FakeFileRepository()
        self.file_text_extractor = _FakeFileTextExtractor(extraction)
        self.committed = False

    def __enter__(self) -> "_FakeUoW":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def test_fake_uow_keeps_typed_exception_context_contract():
    hints = get_type_hints(_FakeUoW.__exit__)
    assert hints == {
        "exc_type": type[BaseException] | None,
        "exc": BaseException | None,
        "traceback": TracebackType | None,
        "return": type(None),
    }


@pytest.mark.parametrize(
    ("extraction", "expected_status"),
    [
        (ports.FileTextExtractionResult("completed", "提取文本", "parser"), "pending"),
        (
            ports.FileTextExtractionResult(
                "failed", "", "ocr", "OCRFailure", "无法识别"
            ),
            "failed",
        ),
    ],
)
def test_file_application_uses_structured_extraction_result(extraction, expected_status):
    uow = _FakeUoW(extraction)
    use_cases = JDUseCases(lambda: uow, object(), object())
    command = JDFileCreateCommand(
        upload=ports.FileUpload("jd.txt", "text/plain", b"content"),
        title="后端工程师",
        source_type="upload",
        source_name=None,
        enterprise_id=None,
    )

    result = use_cases.create_file(ports.Actor("admin-1", "admin"), command)

    assert isinstance(uow.jds.command, ports.JDCreateCommand)
    assert result.raw_text == extraction.text
    assert result.parse_status == expected_status
    assert uow.committed is True


@pytest.mark.parametrize(
    "invalid_result",
    [
        {"legacy": "dict"},
        ports.FileAssetDTO("wrong", "owner", "x", None, "x", 1, None),
        None,
    ],
)
def test_application_rejects_invalid_non_optional_repository_results(invalid_result):
    uow = _FakeUoW(ports.FileTextExtractionResult("completed", "text", "parser"))
    def invalid_create_jd(command):
        return invalid_result
    uow.jds.create_jd = invalid_create_jd
    use_cases = JDUseCases(lambda: uow, object(), object())
    command = JDFileCreateCommand(
        upload=ports.FileUpload("jd.txt", "text/plain", b"content"),
        title="后端工程师", source_type="upload", source_name=None,
        enterprise_id=None,
    )
    with pytest.raises(
        PortContractError,
        match=r"JDRepository\.create_jd expected JDDTO, got",
    ):
        use_cases.create_file(ports.Actor("admin-1", "admin"), command)
    assert uow.committed is False


def test_application_rejects_dictionary_file_extractor_result():
    uow = _FakeUoW(ports.FileTextExtractionResult("completed", "text", "parser"))
    def invalid_extract_text(file_asset, use_ocr):
        return {"status": "completed", "text": "legacy", "provider": "legacy"}
    uow.file_text_extractor.extract_text = invalid_extract_text
    use_cases = JDUseCases(lambda: uow, object(), object())
    command = JDFileCreateCommand(
        upload=ports.FileUpload("jd.txt", "text/plain", b"content"),
        title="后端工程师", source_type="upload", source_name=None,
        enterprise_id=None,
    )
    with pytest.raises(
        PortContractError,
        match=r"FileTextExtractor\.extract_text expected FileTextExtractionResult, got dict",
    ):
        use_cases.create_file(ports.Actor("admin-1", "admin"), command)


def test_application_preserves_legal_optional_none_as_not_found():
    uow = _FakeUoW(ports.FileTextExtractionResult("completed", "text", "parser"))
    def missing_jd(jd_id):
        return None
    uow.jds.get_jd = missing_jd
    use_cases = JDUseCases(lambda: uow, object(), object())
    with pytest.raises(JDApplicationError) as exc_info:
        use_cases.get_jd(ports.Actor("admin-1", "admin"), "missing")
    assert exc_info.value.error_code == "not_found"


@pytest.mark.parametrize("invalid", [{"legacy": "dict"}, ports.Actor("x", "admin")])
def test_application_rejects_invalid_parse_result_types(invalid):
    uow = _FakeUoW(ports.FileTextExtractionResult("completed", "text", "parser"))
    def invalid_parse_result(jd_id):
        return invalid
    uow.jds.get_parse_result = invalid_parse_result
    use_cases = JDUseCases(lambda: uow, object(), object())
    with pytest.raises(
        PortContractError,
        match=r"JDRepository\.get_parse_result expected JDParseResultDTO, got",
    ):
        use_cases._get_parse_result(uow, "jd-1")


@pytest.mark.parametrize("invalid", [{"legacy": "dict"}, ports.Actor("x", "admin")])
def test_application_rejects_invalid_task_result_types(invalid):
    uow = _FakeUoW(ports.FileTextExtractionResult("completed", "text", "parser"))
    uow.tasks = SimpleNamespace(get_task=lambda task_id: invalid)
    use_cases = JDUseCases(lambda: uow, object(), object())
    with pytest.raises(
        PortContractError,
        match=r"TaskRepository\.get_task expected TaskDTO, got",
    ):
        use_cases.get_parse_task(ports.Actor("admin-1", "admin"), "task-1")


def _application_jd() -> ports.JDDTO:
    now = datetime.now(timezone.utc)
    return ports.JDDTO(
        "jd-1", "manual", None, None, "Engineer", "Build systems", None,
        None, None, "pending", "not_required", "direct_text", None, None,
        None, None, False, now, now,
    )


def _application_parse_result() -> ports.JDParseResultDTO:
    return ports.JDParseResultDTO(
        "parse-1", "jd-1", "Engineer", (), (), (), None, None, None, (), (),
        0.9, False, {"document_id": "jd-1"}, {"document_id": "jd-1"},
        None, "v2", "v2", "draft", None, None,
    )


@pytest.mark.parametrize("invalid", [{"bad": 1}, ports.Actor("x", "admin"), None])
@pytest.mark.parametrize("invalid_update_number", [1, 2])
def test_parse_application_checks_ignored_running_and_completed_updates(
    invalid, invalid_update_number
):
    jd = _application_jd()
    parse_result = _application_parse_result()
    bundle = ports.JDSchemaBundle(
        Document(jd.id, "v2"), NormalizationResult(jd.id, "v2")
    )

    class Repository:
        def __init__(self) -> None:
            self.update_count = 0

        def update_jd(self, jd_id, command):
            self.update_count += 1
            return invalid if self.update_count == invalid_update_number else jd

        def flush(self):
            return None

        def update_cleaned_text(self, jd_id, cleaned_text):
            return None

        def upsert_parse_result(self, jd_id, command):
            return parse_result

    class Schema:
        def load(self, extraction_payload, normalization_payload):
            return bundle

        def build(self, document_id, raw_text, fallback_title):
            return bundle

        def persist(self, value):
            return ports.JDSchemaPersistence(
                {"document_id": jd.id}, {"document_id": jd.id}, "v2", "v2"
            )

        def legacy(self, value, fallback_title):
            return ports.JDLegacyFields("Engineer", (), (), (), None, None, None, ())

    uow = SimpleNamespace(
        jds=Repository(),
        catalog_entries=lambda: ((), ()),
        catalog_identity=lambda: CatalogIdentity(
            "skill-catalog.test", "0" * 64
        ),
        position_catalog_identity=lambda: CatalogIdentity(
            "position-taxonomy.v3.0.0", "0" * 64
        ),
        review_tasks=SimpleNamespace(ensure_active=lambda *args, **kwargs: None),
    )
    use_cases = JDUseCases(
        lambda: uow,
        object(),
        Schema(),
        extraction_providers={"rule": RuleBasedJDExtractionProvider()},
    )
    with pytest.raises(
        PortContractError,
        match=r"JDRepository\.update_jd expected JDDTO, got",
    ):
        use_cases._parse_jd(uow, ports.Actor("admin-1", "admin"), jd, "rule")
