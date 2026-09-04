from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Callable

import httpx
import numpy as np

from app.application.contracts import AlgorithmSelection, DiscoveryConfig
from app.application.discovery_identity import normalize_algorithm
from app.domain.discovery import (
    AlgorithmCluster,
    AlgorithmMetadata,
    AlgorithmOutput,
    EmbeddingVector,
    GeneratedDefinition,
    JDSnapshot,
    PositionReference,
    SkillReference,
)
from app.domain.definition_generation import generate_evidence_definition
from app.domain.values import FrozenDict, JsonObject, freeze, thaw
from app.domain.germination import (
    GerminationAssessmentResult,
    assess_germination,
)
from app.domain.lineage import ClusterLineageSpec, LineageRelation, match_cluster_lineage
from app.domain.occupation_clustering import cluster_by_occupation
from app.application.discovery_mapping import structured_data_contract
from app.infrastructure.emergence_v32 import KnowledgeGraphEmergenceV32Client
from app.ports.providers import (
    ClusteringPort,
    DefinitionPort,
    EmbeddingPort,
    GerminationPort,
)


class PositionReferenceError(RuntimeError):
    """Stable error raised when the formal position catalogue is unavailable."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _coerce_snapshot(item: JDSnapshot | Mapping[str, object]) -> JDSnapshot:
    if not isinstance(item, JDSnapshot):
        raise TypeError("discovery algorithms require JDSnapshot values")
    from app.application.discovery_mapping import normalize_snapshot

    return normalize_snapshot(item)


def _skill_names(snapshot: JDSnapshot) -> set[str]:
    structured = snapshot.structured_data
    values: set[str] = set()
    for item in structured.required_skills + structured.bonus_skills:
        value = item.identity
        if value:
            values.add(str(value).strip().casefold())
    return values


def _document(snapshot: JDSnapshot) -> str:
    structured = snapshot.structured_data
    parts: list[str] = [snapshot.title]
    if structured.position_title:
        parts.append(structured.position_title)
    if structured.industry:
        parts.append(structured.industry)
    parts.extend(structured.responsibilities)
    parts.extend(structured.business_scenarios)
    parts.extend(sorted(_skill_names(snapshot)))
    return " ".join(parts).casefold()


def _source_platform(snapshot: JDSnapshot) -> str | None:
    value = snapshot.structured_data.extensions.get("source_platform")
    if value is not None and str(value).strip():
        return str(value).strip().casefold()
    return None


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]{2,}", text)
    # Character bigrams make Chinese titles usable without a tokenizer while
    # remaining deterministic and independent of hand-picked domain keywords.
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return words


class TfidfSvdSkillEmbeddingProvider:
    """Reproducible TF-IDF/SVD representation fused with standard skill vectors."""

    version = "tfidf-svd-skill-v1"

    def __init__(self, components: int = 8, text_weight: float = 0.7) -> None:
        self.components = components
        self.text_weight = text_weight

    def embed(self, snapshots: tuple[JDSnapshot, ...]) -> tuple[tuple[float, ...], ...]:
        snapshots = tuple(_coerce_snapshot(item) for item in snapshots)
        documents = [_tokens(_document(item)) for item in snapshots]
        vocabulary = sorted({token for document in documents for token in document})
        skill_vocabulary = sorted(set().union(*(_skill_names(item) for item in snapshots)))
        tfidf = np.zeros((len(snapshots), len(vocabulary)), dtype=float)
        for row, document in enumerate(documents):
            counts = {token: document.count(token) for token in set(document)}
            for column, token in enumerate(vocabulary):
                if token in counts:
                    document_frequency = sum(token in other for other in documents)
                    tfidf[row, column] = (counts[token] / max(len(document), 1)) * (
                        math.log((1 + len(documents)) / (1 + document_frequency)) + 1
                    )
        if tfidf.size:
            u, singular, _ = np.linalg.svd(tfidf, full_matrices=False)
            width = min(self.components, len(singular))
            text_features = u[:, :width] * singular[:width]
            for column in range(width):
                nonzero = np.flatnonzero(np.abs(text_features[:, column]) > 1e-12)
                if len(nonzero) and text_features[nonzero[0], column] < 0:
                    text_features[:, column] *= -1
        else:
            text_features = np.zeros((len(snapshots), 1), dtype=float)
        skills = np.zeros((len(snapshots), len(skill_vocabulary)), dtype=float)
        for row, snapshot in enumerate(snapshots):
            present = _skill_names(snapshot)
            for column, skill in enumerate(skill_vocabulary):
                skills[row, column] = float(skill in present)

        def normalize(matrix: np.ndarray) -> np.ndarray:
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            return matrix / np.where(norms == 0, 1, norms)

        fused = np.concatenate(
            (
                normalize(text_features) * self.text_weight,
                normalize(skills) * (1 - self.text_weight),
            ),
            axis=1,
        )
        return tuple(tuple(float(value) for value in row) for row in fused.round(12))


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return 0.0 if denominator == 0 else numerator / denominator


class AgglomerativeClusteringAlgorithm:
    """Deterministic average-link clustering over the supplied embeddings."""

    version = "tfidf-svd-skill-agglomerative-v1"

    def __init__(self, similarity_threshold: float = 0.55, random_seed: int = 20260715):
        self.similarity_threshold = similarity_threshold
        self.random_seed = random_seed

    def cluster(
        self,
        snapshots: tuple[JDSnapshot, ...] | list[Mapping[str, object]],
        embeddings: tuple[tuple[float, ...], ...] | list[list[float]],
    ) -> tuple[AlgorithmCluster, ...]:
        snapshots = tuple(_coerce_snapshot(item) for item in snapshots)
        embeddings = tuple(tuple(value) for value in embeddings)
        if len(snapshots) != len(embeddings):
            raise ValueError("embedding count must equal snapshot count")
        clusters = [[index] for index in range(len(snapshots))]
        while len(clusters) > 1:
            candidates: list[tuple[float, tuple[str, ...], int, int]] = []
            for left in range(len(clusters)):
                for right in range(left + 1, len(clusters)):
                    similarities = [
                        _cosine(embeddings[a], embeddings[b])
                        for a in clusters[left]
                        for b in clusters[right]
                    ]
                    score = sum(similarities) / len(similarities)
                    ids = tuple(
                        sorted(snapshots[index].jd_id for index in clusters[left] + clusters[right])
                    )
                    candidates.append((score, ids, left, right))
            score, _, left, right = max(
                candidates, key=lambda value: (value[0], tuple(reversed(value[1])))
            )
            if score < self.similarity_threshold:
                break
            clusters[left] = sorted(clusters[left] + clusters[right])
            del clusters[right]

        result: list[AlgorithmCluster] = []
        for indices in sorted(
            clusters, key=lambda group: sorted(snapshots[i].jd_id for i in group)
        ):
            members = [snapshots[index] for index in indices]
            skills = sorted(set().union(*(_skill_names(member) for member in members)))
            pair_scores = [
                _cosine(embeddings[left], embeddings[right])
                for offset, left in enumerate(indices)
                for right in indices[offset + 1 :]
            ]
            stability = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
            member_ids = sorted(member.jd_id for member in members)
            key = min(member_ids)
            label = " / ".join(skills[:3]) or "未标准化技能组合"
            centroid = tuple(
                float(value)
                for value in np.mean([embeddings[index] for index in indices], axis=0).round(12)
            )
            result.append(
                AlgorithmCluster(
                    key=key,
                    cluster_name=f"{label} 岗位簇",
                    members=tuple(members),
                    core_skills=tuple(skills[:12]),
                    stability_score=round(stability, 4),
                    centroid=centroid,
                    algorithm_version=self.version,
                    similarity_threshold=self.similarity_threshold,
                    random_seed=self.random_seed,
                )
            )
        return tuple(result)


class PayloadPositionReferenceProvider:
    """Development/test fake. Production composition explicitly rejects it."""

    is_fake = True

    def resolve(self, references: tuple[PositionReference, ...]) -> tuple[PositionReference, ...]:
        return references


class KnowledgeGraphPositionReferenceProvider:
    is_fake = False

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def resolve(self, references: tuple[PositionReference, ...]) -> tuple[PositionReference, ...]:
        try:
            login = httpx.post(
                f"{self.base_url}/api/v1/auth/token",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
            if not 200 <= login.status_code < 300:
                raise PositionReferenceError(
                    "knowledge_graph_auth_error",
                    "position reference authentication failed",
                    {"status_code": login.status_code},
                )
            try:
                token = login.json()["data"]["access_token"]
            except (ValueError, KeyError, TypeError) as exc:
                raise PositionReferenceError(
                    "knowledge_graph_contract_error", "authentication response is missing fields"
                ) from exc
            response = httpx.get(
                f"{self.base_url}/api/v1/integrations/position-references",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise PositionReferenceError(
                "knowledge_graph_timeout", "position reference request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise PositionReferenceError(
                "knowledge_graph_unavailable", "position reference service is unavailable"
            ) from exc
        if not 200 <= response.status_code < 300:
            raise PositionReferenceError(
                "knowledge_graph_http_error",
                "position reference service returned a non-success response",
                {"status_code": response.status_code},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PositionReferenceError(
                "knowledge_graph_invalid_json", "position reference response is invalid JSON"
            ) from exc
        data = body.get("data") if isinstance(body, dict) else None
        if body.get("code") != 0 or not isinstance(data, list):
            raise PositionReferenceError(
                "knowledge_graph_contract_error", "position reference response is missing fields"
            )
        if not data:
            raise PositionReferenceError(
                "knowledge_graph_empty_reference", "no formal position reference is available"
            )
        requested_by_id = {item.position_id: item for item in references}
        resolved: list[PositionReference] = []
        for item in data:
            if not isinstance(item, Mapping) or not item.get("position_id"):
                continue
            position_id = str(item["position_id"])
            returned_skills = tuple(
                SkillReference(
                    raw_skill=str(skill["raw_skill"]) if skill.get("raw_skill") else None,
                    normalized_skill_id=(
                        str(skill["normalized_skill_id"])
                        if skill.get("normalized_skill_id")
                        else None
                    ),
                )
                for skill in item.get("required_skills", ())
                if isinstance(skill, Mapping)
            )
            requested = requested_by_id.get(position_id)
            # Some catalog projections intentionally expose identity/version
            # before their skill edges are published. Keep the caller's
            # immutable skill snapshot in that narrow case so distance scoring
            # remains computable without treating an incomplete catalog row as
            # a reference with zero skills.
            resolved.append(
                PositionReference(
                    position_id=position_id,
                    required_skills=returned_skills or (requested.required_skills if requested else ()),
                    graph_version_id=str(item["graph_version_id"]),
                )
            )
        return tuple(resolved)


class EvidenceDefinitionGenerator:
    version = "evidence-definition-v4"

    def generate(
        self, cluster: AlgorithmCluster, reference_skill_sets: list[set[str]]
    ) -> GeneratedDefinition:
        return generate_evidence_definition(cluster, reference_skill_sets)


class DomainGerminationPolicy:
    def assess(
        self,
        *,
        sample_count: int,
        effective_sample_count: int,
        sources: list[str],
        enterprises: list[str | None],
        spread_labels: list[str],
        publish_dates: list[date],
        all_publish_dates: list[date],
        candidate_skills: set[str],
        reference_skill_sets: list[set[str]],
        stability_score: float,
        config: DiscoveryConfig,
        window_ids: list[str],
        all_window_ids: list[str],
        evidence_quality: JsonObject,
        required_window_ids: list[str],
    ) -> GerminationAssessmentResult:
        return assess_germination(
            sample_count=sample_count,
            effective_sample_count=effective_sample_count,
            sources=sources,
            enterprises=enterprises,
            spread_labels=spread_labels,
            publish_dates=publish_dates,
            all_publish_dates=all_publish_dates,
            candidate_skills=candidate_skills,
            reference_skill_sets=reference_skill_sets,
            stability_score=stability_score,
            config=thaw(config.values),
            window_ids=window_ids,
            all_window_ids=all_window_ids,
            evidence_quality=evidence_quality,
            required_window_ids=required_window_ids,
        )


def _canonical_value(value):
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _canonical_value(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_value(item) for item in value)
    return value


class DomainLineageMatcher:
    def match(
        self,
        previous: list[ClusterLineageSpec],
        current: list[ClusterLineageSpec],
    ) -> list[LineageRelation]:
        return match_cluster_lineage(previous, current)


def _emergence_v32_assessment(
    result: dict[str, Any],
    diagnostic: GerminationAssessmentResult,
) -> GerminationAssessmentResult:
    state = str(result.get("state") or "insufficient_evidence")
    return GerminationAssessmentResult(
        germination_score=diagnostic.germination_score,
        dimensions=diagnostic.dimensions,
        level=state,
        qualified_as_emerging=state == "emerging",
        decision_reason=str(result.get("reason") or state),
        weights=diagnostic.weights,
        thresholds=diagnostic.thresholds,
        evidence_summary=FrozenDict(
            {
                **dict(diagnostic.evidence_summary),
                "emergence_v3_2": freeze(result),
            }
        ),
        formula_version="emerge-v3.2",
    )


class SelectableDiscoveryAlgorithm:
    """Infrastructure algorithm registry selected by the public algorithm field."""

    def __init__(
        self,
        embedding: EmbeddingPort | None = None,
        clustering_factory: Callable[[float], ClusteringPort] | None = None,
        definitions: DefinitionPort | None = None,
        germination: GerminationPort | None = None,
        emergence_v32: KnowledgeGraphEmergenceV32Client | None = None,
    ) -> None:
        self.embedding = embedding or TfidfSvdSkillEmbeddingProvider()
        self.clustering_factory = clustering_factory or (
            lambda threshold: AgglomerativeClusteringAlgorithm(similarity_threshold=threshold)
        )
        self.definitions = definitions or EvidenceDefinitionGenerator()
        self.germination = germination or DomainGerminationPolicy()
        self.emergence_v32 = emergence_v32

    def execute(
        self,
        *,
        algorithm: AlgorithmSelection,
        snapshots: tuple[JDSnapshot, ...],
        reference_skill_sets: list[set[str]],
        config: DiscoveryConfig,
        time_window_ids: list[str] | None = None,
    ) -> AlgorithmOutput:
        if isinstance(algorithm, str):
            algorithm = normalize_algorithm(algorithm)
        if not isinstance(config, DiscoveryConfig):
            frozen = freeze(config)
            if not isinstance(frozen, FrozenDict):
                raise TypeError("discovery config must be a JSON object")
            config = DiscoveryConfig(frozen)
        snapshots = tuple(_coerce_snapshot(item) for item in snapshots)
        threshold = algorithm.similarity_threshold.value
        metadata_extensions: JsonObject = FrozenDict()
        if algorithm.canonical_name != "emerge_v3_2":
            raise ValueError(f"unsupported formal algorithm: {algorithm.canonical_name}")
        embeddings = self.embedding.embed(snapshots)
        grouped = cluster_by_occupation(snapshots, embeddings)
        clustering_name = "EmergenceV32OccupationCluster"
        clustering_version = "emerge-v3.2"
        random_seed = 20260823
        feature_version = "occupation-title-key-v1+emerge-v3.2-stage1-bge-m3"
        metadata_extensions = FrozenDict(
            {
                "candidate_clustering": "occupation-title-key-v1",
                "stage2_unit": "occupation_cluster",
                "semantic_role": "stage1_mature_reference_comparison_only",
                "generic_term_transitive_merge": "disabled",
                "split_refinement": FrozenDict(
                    {
                        "enabled": False,
                        "reason": "formal_stage2_unit_is_frozen_occupation_cluster",
                    }
                ),
                "decision_policy": "emerge_v3_2",
            }
        )
        if self.emergence_v32 is None:
            raise RuntimeError("EMERGE v3.2 knowledge-graph gateway is required")
        dataset_id = str(config.values.get("dataset_id") or "").strip()
        if not dataset_id:
            raise ValueError("EMERGE v3.2 requires dataset_id")
        v32_clusters: list[dict[str, Any]] = []
        for cluster in grouped:
            members = []
            for member in cluster.members:
                if not member.publish_date or not member.source_record_id or not member.content_hash:
                    raise ValueError(
                        "EMERGE v3.2 requires observation date, source_record_id and content_hash"
                    )
                extensions = member.structured_data.extensions
                members.append(
                    {
                        "document_id": member.jd_id,
                        "source_record_id": member.source_record_id,
                        "content_hash": member.content_hash,
                        "observation_date": member.publish_date.isoformat(),
                        "date_source": member.date_source or "publish_date",
                        "company": next(
                            (
                                str(extensions[key])
                                for key in ("company_id", "company_name", "enterprise_id")
                                if extensions.get(key)
                            ),
                            None,
                        ),
                        "source_platform": extensions.get("source_platform"),
                        "bundle_id": member.bundle_id,
                        "evidence_refs": list(extensions.get("evidence_ids") or ()),
                    }
                )
            v32_clusters.append(
                {
                    "cluster_id": cluster.key,
                    "title": cluster.cluster_name,
                    "skills": list(cluster.core_skills),
                    "responsibilities": list(cluster.core_responsibilities),
                    "members": members,
                }
            )
        v32_results = self.emergence_v32.evaluate(
            dataset_id=dataset_id,
            clusters=v32_clusters,
        )

        all_dates = [item.publish_date for item in snapshots if item.publish_date]
        all_window_ids = [item.window_id for item in snapshots]
        prepared: list[AlgorithmCluster] = []
        for item in grouped:
            members = item.members
            content_keys = {
                _canonical_value((member.title, structured_data_contract(member)))
                for member in members
            }
            sources = [_source_platform(member) or "unknown" for member in members]
            enterprises = [
                next(
                    (
                        str(member.structured_data.extensions[key]).strip()
                        for key in ("enterprise_id", "company_id", "company_name")
                        if member.structured_data.extensions.get(key)
                    ),
                    None,
                )
                for member in members
            ]
            spread: list[str] = []
            dates = []
            candidate_skills: set[str] = set()
            for member in members:
                structured = member.structured_data
                spread.extend(structured.business_scenarios)
                if structured.industry:
                    spread.append(structured.industry)
                if member.publish_date:
                    dates.append(member.publish_date)
                for skill in structured.required_skills + structured.bonus_skills:
                    identity = skill.identity
                    if identity and str(identity).strip():
                        candidate_skills.add(str(identity).strip().casefold())
            diagnostic = self.germination.assess(
                sample_count=len(members),
                effective_sample_count=len(content_keys),
                sources=sources,
                enterprises=enterprises,
                spread_labels=spread,
                publish_dates=dates,
                all_publish_dates=all_dates,
                candidate_skills=candidate_skills,
                reference_skill_sets=reference_skill_sets,
                stability_score=item.stability_score,
                config=config,
                window_ids=[member.window_id for member in members],
                all_window_ids=all_window_ids,
                evidence_quality=FrozenDict(
                    {
                        "evidence_count_score": min(
                            1.0,
                            sum(
                                len(member.structured_data.extensions.get("evidence_ids", ()))
                                for member in members
                            )
                            / max(len(members), 1),
                        ),
                        "field_coverage": sum(
                            sum(
                                (
                                    bool(member.title),
                                    bool(member.structured_data.responsibilities),
                                    bool(member.structured_data.required_skills),
                                    bool(_source_platform(member)),
                                    bool(member.publish_date),
                                )
                            )
                            / 5
                            for member in members
                        )
                        / max(len(members), 1),
                        "source_reliability": sum(
                            member.review_status == "published" for member in members
                        )
                        / max(len(members), 1),
                        "original_text_locatability": sum(
                            bool(member.source_fact_id and member.source_fact_version)
                            for member in members
                        )
                        / max(len(members), 1),
                    }
                ),
                required_window_ids=(time_window_ids or list(dict.fromkeys(all_window_ids))),
            )
            assessment = _emergence_v32_assessment(v32_results[item.key], diagnostic)
            prepared.append(
                AlgorithmCluster(
                    key=item.key,
                    cluster_name=item.cluster_name,
                    members=item.members,
                    core_skills=item.core_skills,
                    stability_score=item.stability_score,
                    centroid=item.centroid,
                    algorithm_version=item.algorithm_version,
                    similarity_threshold=item.similarity_threshold,
                    random_seed=item.random_seed,
                    core_responsibilities=item.core_responsibilities,
                    semantic_centroid=item.semantic_centroid,
                    algorithm_sources=(item.algorithm_sources or ("tfidf_skill_baseline",)),
                    merge_basis=(
                        item.merge_basis
                        or FrozenDict(
                            {
                                "rule": "baseline_agglomerative_similarity_threshold",
                                "threshold": threshold,
                            }
                        )
                    ),
                    assessment=assessment,
                    generated_definition=self.definitions.generate(item, reference_skill_sets),
                )
            )
        metadata = AlgorithmMetadata(
            algorithm_name=clustering_name,
            requested_algorithm=algorithm.requested_name,
            algorithm_version=clustering_version,
            feature_version=feature_version,
            similarity_threshold=threshold,
            random_seed=random_seed,
            extensions=metadata_extensions,
        )
        return AlgorithmOutput(
            algorithm_version=f"{clustering_version}:{algorithm.canonical_name}",
            formula_version="emerge-v3.2",
            metadata=metadata,
            embeddings=tuple(
                EmbeddingVector(item.jd_id, vector)
                for item, vector in zip(snapshots, embeddings, strict=True)
            ),
            clusters=tuple(prepared),
        )
