"""D21 read-only single-Candidate observation diffusion graph."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.domain.candidate_identity import dedup_cluster_identity
from app.domain.values import FrozenDict, JsonObject, freeze, thaw
from app.ports.providers import DiscoveryUnitOfWork
from app.ports.records import CandidateDiffusionRecord


GRAPH_SCHEMA_VERSION = "candidate-diffusion-graph.v1"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entity_key(value: str | None, observation_id: str) -> tuple[str, str]:
    return ("value", value) if value else ("missing", observation_id)


def _node_id(kind: str, key: tuple[str, str]) -> str:
    digest = hashlib.sha256(
        json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"{kind}:{digest}"


def _trace(observation, context, candidate_version: str) -> dict[str, Any]:
    return {
        "observation_id": observation.id,
        "run_id": observation.run_id,
        "cluster_id": observation.cluster_id,
        "window_id": observation.window_id,
        "observed_at": context.observed_at.isoformat(),
        "candidate_id": observation.candidate_id,
        "candidate_version": candidate_version,
        "algorithm_version": context.algorithm_version,
        "formula_version": context.formula_version,
        "config_snapshot_id": context.config_snapshot_id,
        "config_version": context.config_version,
    }


def build_candidate_diffusion_graph(record: CandidateDiffusionRecord) -> JsonObject:
    candidate = record.candidate
    candidate_version = _canonical_hash(
        {
            "candidate_id": candidate.id,
            "first_seen_window_id": candidate.first_seen_window_id,
            "last_seen_window_id": candidate.last_seen_window_id,
            "observed_window_ids": candidate.observed_window_ids,
            "identity_certificate": thaw(candidate.identity_certificate),
        }
    )
    ordered = sorted(
        record.observations,
        key=lambda item: (
            item.observed_at,
            item.observation.window_id,
            item.observation.id,
        ),
    )
    candidate_node_id = f"candidate:{candidate.id}"
    nodes: dict[str, dict[str, Any]] = {
        candidate_node_id: {
            "node_id": candidate_node_id,
            "node_type": "candidate",
            "candidate_id": candidate.id,
            "candidate_version": candidate_version,
            "first_seen_window_id": candidate.first_seen_window_id,
            "last_seen_window_id": candidate.last_seen_window_id,
            "identity_certificate": thaw(candidate.identity_certificate),
        }
    }
    edges: list[dict[str, Any]] = []
    window_sequence: list[str] = []
    for sequence, context in enumerate(ordered):
        observation = context.observation
        observation_node_id = f"observation:{observation.id}"
        trace = _trace(observation, context, candidate_version)
        if observation.window_id not in window_sequence:
            window_sequence.append(observation.window_id)
        nodes[observation_node_id] = {
            "node_id": observation_node_id,
            "node_type": "observation",
            "sequence": sequence,
            "status": observation.status,
            "title": observation.title,
            "trace": trace,
        }
        edges.append(
            {
                "edge_id": f"candidate-observation:{observation.id}",
                "edge_type": "candidate_observation",
                "from_node_id": candidate_node_id,
                "to_node_id": observation_node_id,
                "trace": trace,
            }
        )
        grouped: dict[tuple[str, tuple[str, str]], list[Any]] = defaultdict(list)
        # Exact repeated memberships are collapsed, while company and source
        # dimensions stay independent so their many-to-many spread is retained.
        unique_evidence = {
            (
                item.input_snapshot_id,
                item.source_fact_id,
                item.input_version,
                item.source_name,
                item.company,
            ): item
            for item in context.evidence
        }.values()
        for item in unique_evidence:
            grouped[("company", _entity_key(item.company, observation.id))].append(item)
            grouped[("source", _entity_key(item.source_name, observation.id))].append(item)
        for (kind, key), evidence in sorted(grouped.items(), key=lambda item: item[0]):
            node_id = _node_id(kind, key)
            status, value = key
            nodes.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "node_type": kind,
                    "identity_status": status,
                    "value": value if status == "value" else None,
                },
            )
            refs = sorted(
                {
                    f"{item.source_fact_id}:{item.input_version}"
                    for item in evidence
                }
            )
            source_facts = sorted(
                {
                    (item.source_fact_id, item.input_version, item.source_jd_id)
                    for item in evidence
                }
            )
            dedup_ids = sorted(
                {
                    dedup_cluster_identity(item.content_hash)
                    for item in evidence
                    if item.content_hash
                }
            )
            edge_type = "company_adoption" if kind == "company" else "source_spread"
            edges.append(
                {
                    "edge_id": f"{edge_type}:{observation.id}:{node_id}",
                    "edge_type": edge_type,
                    "from_node_id": observation_node_id,
                    "to_node_id": node_id,
                    "identity_status": status,
                    "evidence_refs": refs,
                    "source_facts": [
                        {
                            "source_fact_id": fact_id,
                            "input_version": version,
                            "source_jd_id": jd_id,
                        }
                        for fact_id, version, jd_id in source_facts
                    ],
                    "dedup_identities": dedup_ids,
                    "trace": trace,
                }
            )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        left = previous.observation
        right = current.observation
        edges.append(
            {
                "edge_id": f"temporal:{left.id}:{right.id}",
                "edge_type": "temporal_diffusion",
                "from_node_id": f"observation:{left.id}",
                "to_node_id": f"observation:{right.id}",
                "same_time": previous.observed_at == current.observed_at,
                "same_window": left.window_id == right.window_id,
                "cross_window": left.window_id != right.window_id,
                "from_trace": _trace(left, previous, candidate_version),
                "to_trace": _trace(right, current, candidate_version),
                "interpretation": "descriptive_sequence_only",
            }
        )
    graph_content = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "readonly": True,
        "scope": "single_candidate_observation_diffusion",
        "candidate_id": candidate.id,
        "candidate_version": candidate_version,
        "window_sequence": window_sequence,
        "nodes": sorted(nodes.values(), key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "boundaries": {
            "market_trend": False,
            "industry_evolution": False,
            "causal_diffusion": False,
            "emerging_market_conclusion": False,
        },
    }
    value = freeze({**graph_content, "graph_identity": _canonical_hash(graph_content)})
    assert isinstance(value, FrozenDict)
    return value


@dataclass(frozen=True)
class QueryCandidateDiffusion:
    uow: DiscoveryUnitOfWork

    def execute(self, candidate_id: str) -> JsonObject:
        with self.uow:
            record = self.uow.candidates.candidate_diffusion(candidate_id)
        return build_candidate_diffusion_graph(record)
