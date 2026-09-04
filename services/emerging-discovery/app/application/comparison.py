"""Side-by-side clustering evaluation without creating discovery runs."""

from __future__ import annotations

from app.application.contracts import (
    AlgorithmComparisonResult,
    RunDiscoveryCommand,
)
from app.application.discovery_mapping import normalize_snapshot
from app.application.input_quality import precheck_discovery_input
from app.domain.values import FrozenDict, JsonObject
from app.ports.providers import AlgorithmRegistryPort


class CompareAlgorithms:
    def __init__(self, registry: AlgorithmRegistryPort) -> None:
        self.registry = registry

    def execute(
        self,
        command: RunDiscoveryCommand,
        algorithms: tuple[str, ...],
        algorithm_configs: JsonObject,
    ) -> AlgorithmComparisonResult:
        precheck = precheck_discovery_input(
            tuple(normalize_snapshot(item) for item in command.snapshots),
            time_window_start=command.time_window_start,
            time_window_end=command.time_window_end,
        )
        if not precheck.snapshots:
            raise ValueError("no JD snapshots remain after input precheck")
        available = set(self.registry.names())
        unknown = sorted(set(algorithms) - available)
        if unknown:
            raise ValueError(f"unsupported comparison algorithm: {unknown[0]}")
        results = []
        for name in algorithms:
            raw_parameters = algorithm_configs.get(name, FrozenDict())
            if not isinstance(raw_parameters, FrozenDict):
                raise ValueError(f"algorithm config for {name} must be an object")
            results.append(self.registry.evaluate(name, precheck.snapshots, raw_parameters))
        recommended = max(
            results,
            key=lambda item: (item.recommendation_score, item.algorithm),
        )
        reason = (
            f"{recommended.algorithm} has the highest experimental score "
            f"({recommended.recommendation_score:.4f}); stability="
            f"{float(recommended.stability_analysis.get('stability_score', 0.0)):.4f}, "
            f"noise_ratio={recommended.noise_ratio:.4f}"
        )
        return AlgorithmComparisonResult(
            contract_version=command.contract_version,
            request_id=command.request_id,
            input_quality_report=precheck.report,
            algorithms=tuple(results),
            recommended_algorithm=recommended.algorithm,
            recommendation_reason=reason,
        )
