"""Generate or verify the Phase 0 contract and configuration structures."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
INVENTORY_PATH = ROOT / "config" / "phase0_contract_inventory.json"
BASELINE_PATH = ROOT / "config" / "phase0_contract_baseline.json"
DEFAULT_REPOSITORY_ROOT = ROOT.parents[2]


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def normalize_json_schema(value: Any) -> Any:
    """Remove explicit JSON Schema defaults that vary across Pydantic versions."""
    if isinstance(value, dict):
        return {
            key: normalize_json_schema(child)
            for key, child in value.items()
            if not (key == "additionalProperties" and child is True)
        }
    if isinstance(value, list):
        return [normalize_json_schema(child) for child in value]
    return value


def load_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def artifact_bytes(path: Path, format_name: str) -> bytes:
    if format_name == "json":
        return canonical_json(json.loads(path.read_text(encoding="utf-8")))
    if format_name == "yaml":
        return canonical_json(yaml.safe_load(path.read_text(encoding="utf-8")))
    if format_name == "text":
        return path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    raise ValueError(f"unsupported artifact format: {format_name}")


def resolve_model(reference: str):
    module_name, separator, name = reference.partition(":")
    if not separator or not module_name or not name:
        raise ValueError(f"invalid model reference: {reference}")
    return getattr(importlib.import_module(module_name), name)


def collect_baseline(
    *,
    include_external: bool,
    repository_root: Path = DEFAULT_REPOSITORY_ROOT,
) -> dict[str, Any]:
    inventory = load_inventory()
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact in inventory["artifacts"]:
        if artifact["location"] == "repository" and not include_external:
            continue
        base = ROOT if artifact["location"] == "local" else repository_root
        path = base / artifact["path"]
        if not path.is_file():
            raise FileNotFoundError(f"contract artifact is missing: {path}")
        value = artifact_bytes(path, artifact["format"])
        artifacts[artifact["id"]] = {
            "content": value.decode("utf-8"),
        }

    runtime_contracts: dict[str, dict[str, Any]] = {}
    for contract in inventory["runtime_contracts"]:
        model = resolve_model(contract["model"])
        actual_fields = list(model.model_fields)
        if actual_fields != contract["root_fields"]:
            raise ValueError(
                f"{contract['id']} root fields changed: "
                f"expected {contract['root_fields']}, got {actual_fields}"
            )
        schema = normalize_json_schema(model.model_json_schema())
        runtime_contracts[contract["id"]] = {
            "schema": schema,
            "root_fields": actual_fields,
            "required_fields": schema.get("required", []),
        }

    declarative_contracts = {
        contract["id"]: {
            "contract": contract,
            "root_fields": contract["root_fields"],
        }
        for contract in inventory["declarative_contracts"]
    }
    return {
        "baseline_version": "kg-phase0-contract-baseline.v1",
        "inventory": inventory,
        "artifacts": artifacts,
        "runtime_contracts": runtime_contracts,
        "declarative_contracts": declarative_contracts,
    }


def expected_for(actual: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    expected["artifacts"] = {
        key: expected["artifacts"][key] for key in actual["artifacts"]
    }
    for contract in expected["runtime_contracts"].values():
        contract["schema"] = normalize_json_schema(contract["schema"])
    return expected


def verify_baseline(
    *,
    include_external: bool,
    repository_root: Path = DEFAULT_REPOSITORY_ROOT,
) -> dict[str, Any]:
    actual = collect_baseline(
        include_external=include_external,
        repository_root=repository_root,
    )
    expected = expected_for(actual)
    if actual != expected:
        raise RuntimeError(
            "Phase 0 contract baseline drifted. Review the contract change and "
            "regenerate explicitly with --write.\n"
            f"EXPECTED={json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n"
            f"ACTUAL={json.dumps(actual, ensure_ascii=False, sort_keys=True)}"
        )
    return actual


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    parser.add_argument("--include-external", action="store_true")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=DEFAULT_REPOSITORY_ROOT,
    )
    args = parser.parse_args()
    repository_root = args.repository_root.resolve()
    if args.write:
        baseline = collect_baseline(
            include_external=True,
            repository_root=repository_root,
        )
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "written", **baseline}, ensure_ascii=False))
        return
    baseline = verify_baseline(
        include_external=args.include_external,
        repository_root=repository_root,
    )
    print(json.dumps({"status": "verified", **baseline}, ensure_ascii=False))


if __name__ == "__main__":
    main()
