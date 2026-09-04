"""RAG-QA-HARD-01 v4 hybrid retrieval evaluation with pipeline ablations.

Pipeline: metadata hard filter -> BM25 + dense -> RRF fusion -> reranker ->
evidence sufficiency gate -> answer/abstain.  Ablations compare dense-only,
BM25-only, hybrid, hybrid+rerank and hybrid+rerank+gate.  Dense scoring can use
the real ``embedding-service.v1`` endpoint when configured; otherwise it falls
back to the deterministic lexical proxy and the report says so explicitly.
The QueryIntent parser is standalone and no longer imports benchmark
``TECH_TERMS``, and visible-evidence overrides are applied per case so stale
cases cannot contaminate the shared candidate pool.
"""

from __future__ import annotations

import argparse
import httpx
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rag_snapshot_versions import graph_version_by_document

from cached_catalog_embedding import CachedCatalogEmbedding
from evaluate_d4_normalization import load_catalog
from app.contexts.catalog._applications.normalization_suggestions import (
    rank_normalization_suggestions,
)
from app.domain.skills import normalize_skill_expression

RRF_K = 60
TOP_K = 5
FRESHNESS_CUTOFF_DAYS = 365
CANDIDATE_K = 20
RERANK_WEIGHTS = {
    "bm25": 0.30,
    "dense": 0.30,
    "concept_coverage": 0.25,
    "exact_term_bonus": 0.10,
    "freshness": 0.05,
}
RESOLVER_EVIDENCE_ALPHA = 0.5
RESOLVER_JOINT_THRESHOLD = 0.55
EMBEDDING_BATCH_SIZE = 32


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "innovation"
            / "RAG-QA-HARD-01"
            / "v3"
            / "qa-manifest.json"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "innovation"
            / "RAG-QA-HARD-01"
            / "v3"
        ),
    )
    parser.add_argument(
        "--db",
        default=str(
            Path(__file__).resolve().parents[1]
            / "demo-snapshot"
            / "knowledge-graph"
            / "knowledge-graph.db"
        ),
    )
    parser.add_argument("--embedding-url", default=None)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--embedding-revision", default=None)
    parser.add_argument("--embedding-dimension", type=int, default=None)
    parser.add_argument("--resolver-confidence", type=float, default=0.70)
    parser.add_argument("--resolver-margin", type=float, default=0.10)
    parser.add_argument("--resolver-semantic-threshold", type=float, default=0.55)
    parser.add_argument("--resolver-support-threshold", type=float, default=0.65)
    parser.add_argument("--resolver-evidence-alpha", type=float, default=0.5)
    parser.add_argument("--resolver-joint-threshold", type=float, default=0.55)
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    cases = manifest["cases"]
    pool = _evidence_pool(cases, Path(args.db))
    embedding = _build_embedding(args)
    resolver = (
        _ConceptResolver(
            embedding,
            min_confidence=args.resolver_confidence,
            min_margin=args.resolver_margin,
            min_semantic_confidence=args.resolver_semantic_threshold,
        )
        if embedding is not None
        else None
    )
    dense_cache: dict[str, list[tuple[str, float]]] = {}
    variants = (
        "dense_only",
        "bm25_only",
        "hybrid",
        "hybrid_rerank",
        "hybrid_rerank_gate",
    )
    variant_reports = {
        variant: _evaluate(
            cases,
            pool,
            variant,
            embedding,
            dense_cache=dense_cache,
            resolver=resolver,
            resolver_support_threshold=args.resolver_support_threshold,
            resolver_evidence_alpha=args.resolver_evidence_alpha,
            resolver_joint_threshold=args.resolver_joint_threshold,
        )
        for variant in variants
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "rag-hard-hybrid-eval.v4",
        "experiment_id": "RAG-QA-HARD-01",
        "dataset_version": manifest.get("dataset_version"),
        "gold_status": manifest.get("gold_status"),
        "gold_note": (
            "metrics use deterministic same-document scenario suggestions "
            "reviewed as AI proxy; human gold is not claimed"
        ),
        "dense_mode": (
            "production embedding-service.v1"
            if embedding is not None
            else "offline deterministic lexical/substring/bigram proxy "
            "(not the production embedding service)"
        ),
        "query_intent_mode": (
            "standalone parser v4; no benchmark TECH_TERMS import"
        ),
        "system_decision_note": (
            "answerability decision reads retrieval + evidence freshness + "
            "concept coverage; broad questions without required concepts are "
            "insufficient_query_specificity"
        ),
        "faithfulness_status": "not_computed",
        "faithfulness_note": (
            "requires generated answer + evidence verifier; retrieval-only "
            "evaluation does not fabricate faithfulness"
        ),
        "pipeline": (
            "metadata hard filter -> positive-score BM25 + dense -> RRF "
            "(hybrid) -> feature rerank (hybrid_rerank) -> concept "
            "sufficiency gate (hybrid_rerank_gate)"
        ),
        "candidate_k": CANDIDATE_K,
        "rrf_positive_score_only": True,
        "rerank_weights": RERANK_WEIGHTS,
        "top_k": TOP_K,
        "resolver_semantic_threshold": args.resolver_semantic_threshold,
        "resolver_support_threshold": args.resolver_support_threshold,
        "resolver_evidence_alpha": args.resolver_evidence_alpha,
        "resolver_joint_threshold": args.resolver_joint_threshold,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "variants": variant_reports,
    }
    (out_dir / "hybrid-eval-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "TAB-RAG-HARD-01.md").write_text(
        _render_table(report), encoding="utf-8"
    )
    print(out_dir / "hybrid-eval-results.json")
    return 0


def _evidence_pool(cases: list[dict], db_path: Path) -> dict[str, dict]:
    """Build the shared snapshot pool; visible overrides stay case-local."""

    del cases

    pool: dict[str, dict] = {}
    if db_path.exists():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT id, document_id, owner_type, owner_ref, quote, start, end,
                       alignment, occurrence_index, created_at
                FROM extraction_evidence
                ORDER BY id
                """
            )
            rows = cur.fetchall()
            version_by_document = graph_version_by_document(cur)
            for row in rows:
                (
                    evidence_id,
                    document_id,
                    owner_type,
                    owner_ref,
                    quote,
                    start,
                    end,
                    alignment,
                    occurrence_index,
                    created_at,
                ) = row
                pool[f"snapshot-evidence:{evidence_id}"] = {
                    "evidence_id": f"snapshot-evidence:{evidence_id}",
                    "document_id": str(document_id),
                    "source_object_type": "extraction_evidence",
                    "source_object_id": str(owner_ref),
                    "source_document_id": str(document_id),
                    "source_version": str(created_at),
                    "quote": str(quote),
                    "location_start": int(start) if start is not None else None,
                    "location_end": int(end) if end is not None else None,
                    "occurrence_index": int(occurrence_index)
                    if occurrence_index is not None
                    else 0,
                    "alignment": str(alignment),
                    "graph_version_id": version_by_document.get(str(document_id)),
                    "tenant_ref": "jobgraph-platform-public",
                    "permission_scope": "platform:public",
                }
        finally:
            con.close()
    return pool


def _tokens(text: str) -> list[str]:
    value = str(text).casefold()
    tokens: list[str] = []
    for piece in re.split(r"[^\w\u4e00-\u9fff]+", value):
        if not piece:
            continue
        # Split mixed Chinese/ASCII runs so "熟悉MySQL" yields "熟悉" and
        # "mysql" as separate lexical tokens.
        ascii_parts = re.findall(r"[a-z0-9]+", piece)
        if ascii_parts:
            tokens.extend(ascii_parts)
        cjk = re.sub(r"[a-z0-9]+", " ", piece).strip()
        if cjk:
            tokens.extend(cjk.split())
    return tokens


def _term_frequencies(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts


def _bm25(query_terms: list[str], doc_tokens: list[str], avg_dl: float, n: int, df: dict[str, int], k1=1.5, b=0.75) -> float:
    doc_len = len(doc_tokens)
    tf = _term_frequencies(doc_tokens)
    score = 0.0
    for term in query_terms:
        term_df = df.get(term, 0)
        if term_df == 0 or term not in tf:
            continue
        idf = math.log(1 + (n - term_df + 0.5) / (term_df + 0.5))
        term_tf = tf[term]
        score += idf * (term_tf * (k1 + 1)) / (
            term_tf + k1 * (1 - b + b * doc_len / max(avg_dl, 1.0))
        )
    return score


def _dense_proxy(query_terms: list[str], doc_tokens: list[str]) -> float:
    """Deterministic dense proxy: token overlap + substring + char bigram."""

    if not query_terms or not doc_tokens:
        return 0.0
    query_tf = _term_frequencies(query_terms)
    doc_tf = _term_frequencies(doc_tokens)
    exact = sum(count * doc_tf.get(term, 0) for term, count in query_tf.items())
    substring = sum(
        0.5
        for term in query_tf
        if any(term in doc_term for doc_term in doc_tokens if doc_term != term)
    )
    query_bigrams = _bigrams("".join(query_tf))
    doc_bigrams = _bigrams("".join(doc_tf))
    bigram_overlap = len(query_bigrams & doc_bigrams) / max(
        len(query_bigrams | doc_bigrams), 1
    )
    raw = exact + substring + bigram_overlap
    query_norm = math.sqrt(sum(count * count for count in query_tf.values()) + 1)
    doc_norm = math.sqrt(sum(count * count for count in doc_tf.values()) + 1)
    return raw / (query_norm * doc_norm)


def _bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _rrf(ranked_lists: list[list[tuple[str, float]]]) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (evidence_id, _score) in enumerate(ranked):
            scores[evidence_id] = scores.get(evidence_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _metadata_filter(case: dict, ref: dict) -> bool:
    requested = case["requested_identity"]
    business_object = requested.get("business_object") or {}
    business_object_id = str(business_object.get("object_id") or "")
    if (
        business_object_id
        and str(ref.get("source_document_id") or "") != business_object_id
    ):
        return False
    if str(ref.get("tenant_ref") or "") != str(requested.get("tenant_ref") or ""):
        return False
    if str(ref.get("permission_scope") or "") != str(requested.get("permission_scope") or ""):
        return False
    requested_version = requested.get("graph_version_id")
    if requested_version is not None and str(ref.get("graph_version_id") or "") != str(requested_version):
        return False
    return True


def _retrieve(
    case: dict,
    pool: dict[str, dict],
    variant: str,
    embedding: _EmbeddingClient | None = None,
    dense_ranked: list[tuple[str, float]] | None = None,
    resolver: _ConceptResolver | None = None,
    resolver_support_threshold: float = 0.65,
) -> list[dict]:
    query = case["query_text"]
    query_terms = _tokens(query)
    case_pool = dict(pool)
    for ref in case["visible_evidence"]:
        case_pool[ref["evidence_id"]] = ref
    candidates = [
        ref for ref in case_pool.values() if _metadata_filter(case, ref)
    ]
    doc_tokens = {ref["evidence_id"]: _tokens(ref.get("quote") or "") for ref in candidates}
    avg_dl = sum(len(value) for value in doc_tokens.values()) / max(len(doc_tokens), 1)
    df: dict[str, int] = {}
    for tokens in doc_tokens.values():
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    n = len(candidates)
    if dense_ranked is None:
        dense_ranked = _dense_ranked(
            case_pool,
            candidates,
            query,
            query_terms,
            doc_tokens,
            embedding,
        )
    bm25_ranked = sorted(
        (
            (
                ref["evidence_id"],
                _bm25(query_terms, doc_tokens[ref["evidence_id"]], avg_dl, n, df),
            )
            for ref in candidates
        ),
        key=lambda item: (-item[1], item[0]),
    )
    # Only positively-scoring documents participate in fusion so unrelated
    # zero-score Evidence cannot earn RRF credit by rank position.
    dense_ranked = [
        item for item in dense_ranked if item[1] > 0
    ][:CANDIDATE_K]
    bm25_ranked = [
        item for item in bm25_ranked if item[1] > 0
    ][:CANDIDATE_K]
    if variant == "dense_only":
        fused = dense_ranked
    elif variant == "bm25_only":
        fused = bm25_ranked
    else:
        fused = _rrf([dense_ranked, bm25_ranked])
    intent = _query_intent(case["query_text"], resolver)
    if variant in {"hybrid_rerank", "hybrid_rerank_gate"}:
        fused = _rerank(
            fused,
            query_terms,
            doc_tokens,
            intent,
            {evidence_id: score for evidence_id, score in bm25_ranked},
            {evidence_id: score for evidence_id, score in dense_ranked},
            {
                evidence_id: case_pool[evidence_id].get("source_version")
                for evidence_id, _score in fused
            },
            resolver=resolver,
            resolver_support_threshold=resolver_support_threshold,
        )
    bm25_by_id = {evidence_id: score for evidence_id, score in bm25_ranked}
    dense_by_id = {evidence_id: score for evidence_id, score in dense_ranked}
    return [
        {
            "evidence_id": evidence_id,
            "score": round(score, 6),
            "quote": case_pool[evidence_id]["quote"],
            "source_version": case_pool[evidence_id].get("source_version"),
            "support_group_ref": (
                case_pool[evidence_id].get("source_object_id")
                or case_pool[evidence_id].get("owner_ref")
                or evidence_id
            ),
            "bm25_score": round(bm25_by_id.get(evidence_id, 0.0), 6),
            "dense_score": round(dense_by_id.get(evidence_id, 0.0), 6),
        }
        for evidence_id, score in fused[:TOP_K]
    ]


def _dense_ranked(
    case_pool: dict[str, dict],
    candidates: list[dict],
    query_text: str,
    query_terms: list[str],
    doc_tokens: dict[str, list[str]],
    embedding: _EmbeddingClient | None,
) -> list[tuple[str, float]]:
    """Rank by real embeddings when configured, otherwise the lexical proxy."""

    if embedding is None:
        return sorted(
            (
                (
                    ref["evidence_id"],
                    _dense_proxy(
                        query_terms, doc_tokens[ref["evidence_id"]]
                    ),
                )
                for ref in candidates
            ),
            key=lambda item: (-item[1], item[0]),
        )
    texts = tuple(str(ref.get("quote") or "") for ref in candidates)
    vectors = _embed_many(embedding, (query_text, *texts))
    query_vector = vectors[0]
    scores: list[tuple[str, float]] = []
    for ref, vector in zip(candidates, vectors[1:], strict=True):
        scores.append(
            (
                ref["evidence_id"],
                sum(
                    left * right
                    for left, right in zip(
                        query_vector, vector, strict=True
                    )
                ),
            )
        )
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _dense_for_case(
    case: dict,
    pool: dict[str, dict],
    embedding: _EmbeddingClient,
) -> list[tuple[str, float]]:
    """Compute dense scores once per case so variants reuse the vectors."""

    case_pool = dict(pool)
    for ref in case["visible_evidence"]:
        case_pool[ref["evidence_id"]] = ref
    candidates = [
        ref for ref in case_pool.values() if _metadata_filter(case, ref)
    ]
    query = case["query_text"]
    query_terms = _tokens(query)
    doc_tokens = {
        ref["evidence_id"]: _tokens(ref.get("quote") or "")
        for ref in candidates
    }
    return _dense_ranked(
        case_pool,
        candidates,
        query,
        query_terms,
        doc_tokens,
        embedding,
    )


def _embed_many(
    embedding: _EmbeddingClient,
    texts: tuple[str, ...],
) -> tuple[tuple[float, ...], ...]:
    """Batch embedding requests while preserving input order."""

    if not texts:
        return ()
    result: list[tuple[float, ...]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        result.extend(embedding.embed_many(batch))
    return tuple(result)


def _build_embedding(args) -> _EmbeddingClient | None:
    url = args.embedding_url
    if not url:
        return None
    missing = [
        name
        for name, value in (
            ("--embedding-model", args.embedding_model),
            ("--embedding-revision", args.embedding_revision),
            ("--embedding-dimension", args.embedding_dimension),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "embedding mode requires " + ", ".join(missing)
        )
    return _EmbeddingClient(
        url,
        model=args.embedding_model,
        revision=args.embedding_revision,
        dimension=args.embedding_dimension,
    )


class _EmbeddingClient:
    """Minimal embedding-service.v1 client for the offline RAG benchmark."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        revision: str,
        dimension: int,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/v1/embeddings"
        self._model = model
        self._revision = revision
        self._dimension = dimension

    def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            vectors.extend(
                self._embed_batch(texts[start : start + EMBEDDING_BATCH_SIZE])
            )
        return tuple(vectors)

    def _embed_batch(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        import time

        for attempt in range(4):
            response = httpx.post(
                self._endpoint,
                json={"inputs": list(texts), "normalize": True},
                timeout=120.0,
            )
            if response.status_code in {429, 502, 503, 504} and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"embedding HTTP {response.status_code}: "
                    f"{response.text[:300]} inputs="
                    f"{[str(item)[:40] for item in texts[:5]]}"
                )
            payload = response.json()
            lineage_ok = (
                payload.get("model_id") == self._model
                and payload.get("model_revision") == self._revision
                and payload.get("dimension") == self._dimension
                and payload.get("normalized") is True
            )
            if not lineage_ok:
                raise RuntimeError("embedding lineage mismatch")
            vectors = payload.get("vectors")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise RuntimeError("embedding response count mismatch")
            return tuple(tuple(vector) for vector in vectors)
        raise RuntimeError("embedding service still unavailable after retries")


@dataclass(frozen=True)
class _CanonicalConcept:
    skill_id: str
    name: str
    aliases: tuple[str, ...]
    confidence: float
    category: str | None = None


@dataclass(frozen=True)
class _ConceptResolution:
    status: str
    skill_id: str | None
    confidence: float
    margin: float
    lexical_score: float
    semantic_score: float
    reason: str
    lexical_semantic_agree: bool = False
    aliases: tuple[str, ...] = ()
    name: str | None = None
    category: str | None = None


class _RagEmbeddingPort:
    """Adapts the RAG embedding client to the Normalization port protocol."""

    def __init__(self, client: _EmbeddingClient) -> None:
        self._client = client

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        empty = [index for index, text in enumerate(texts) if not text.strip()]
        if empty:
            raise RuntimeError(f"empty embedding inputs: {empty}")
        too_long = [
            index for index, text in enumerate(texts) if len(text) > 4096
        ]
        if too_long:
            raise RuntimeError(
                f"embedding inputs too long: {too_long[:10]}"
            )
        return self._client.embed_many(texts)


class _ConceptResolver:
    """Canonical Concept Resolver reusing Normalization dual-recall."""

    def __init__(
        self,
        embedding: _EmbeddingClient,
        *,
        min_confidence: float = 0.70,
        min_margin: float = 0.10,
        min_semantic_confidence: float = 0.55,
    ) -> None:
        skills, aliases = load_catalog()
        self._skills = skills
        self._aliases = aliases
        self._embedding = CachedCatalogEmbedding(
            _RagEmbeddingPort(embedding)
        )
        self._vector_cache: dict[str, tuple[float, ...]] = {}
        self._resolve_cache: dict[str, tuple[_CanonicalConcept, ...]] = {}
        self._quote_vector_cache: dict[str, tuple[float, ...]] = {}
        self._quote_resolution_cache: dict[str, tuple[_ConceptResolution, ...]] = {}
        self._embedding_client = embedding
        self._min_confidence = min_confidence
        self._min_margin = min_margin
        self._min_semantic_confidence = min_semantic_confidence

    def resolve(self, query_text: str) -> tuple[_ConceptResolution, ...]:
        cache_key = (
            query_text,
            self._min_confidence,
            self._min_margin,
            self._min_semantic_confidence,
        )
        cached = self._resolve_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self.resolve_candidates(query_text)
        self._resolve_cache[cache_key] = result
        return result

    def resolve_candidates(
        self, query_text: str
    ) -> tuple[_ConceptResolution, ...]:
        """Collect all candidate concepts before any acceptance decision."""

        exact = self._exact_surface_scan(query_text)
        resolutions: list[_ConceptResolution] = [*exact]
        seen = {item.skill_id for item in exact}
        for phrase in self._surface_phrases(query_text):
            if not phrase.strip():
                continue
            try:
                suggestions = rank_normalization_suggestions(
                    raw_skill=phrase,
                    context=None,
                    skills=self._skills,
                    aliases=self._aliases,
                    reviewed_skill_id=None,
                    top_k=3,
                    embedding=self._embedding,
                    lexical_pool_size=40,
                    semantic_pool_size=63,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"resolver phrase={phrase!r} failed: {exc}"
                ) from exc
            if not suggestions:
                continue
            for rank, top in enumerate(suggestions):
                if top.skill_id in seen:
                    continue
                seen.add(top.skill_id)
                skill = next(
                    (
                        item
                        for item in self._skills
                        if item.skill_id == top.skill_id
                    ),
                    None,
                )
                if skill is None:
                    continue
                aliases = tuple(
                    sorted(
                        {
                            item.alias
                            for item in self._aliases
                            if item.skill_id == top.skill_id
                        }
                    )
                )
                top1_lex = top.lexical_score or 0.0
                top1_sem = top.semantic_score or 0.0
                top1_combined = top.combined_score or 0.0
                second = (
                    suggestions[rank + 1]
                    if rank + 1 < len(suggestions)
                    else None
                )
                margin = top1_combined - (
                    second.combined_score if second is not None else 0.0
                )
                exact_accept = top1_lex >= 0.98
                accepted = exact_accept or (
                    top1_combined >= self._min_confidence
                    and top1_sem >= self._min_semantic_confidence
                    and margin >= self._min_margin
                )
                resolutions.append(
                    _ConceptResolution(
                        status=(
                            "accepted"
                            if accepted
                            else (
                                "review_required"
                                if top1_combined >= 0.55
                                else "unresolved"
                            )
                        ),
                        skill_id=top.skill_id,
                        confidence=round(top1_combined, 6),
                        margin=round(margin, 6),
                        lexical_score=round(top1_lex, 6),
                        semantic_score=round(top1_sem, 6),
                        reason=(
                            "exact_lexical_accept"
                            if exact_accept
                            else "combined_semantic_margin_ok"
                            if accepted
                            else "low_confidence_or_margin"
                        ),
                        lexical_semantic_agree=(
                            any(
                                item.skill_id == top.skill_id
                                for item in exact
                            )
                        ),
                        name=skill.skill_name,
                        aliases=aliases,
                        category=skill.category,
                    )
                )
        result = tuple(
            sorted(
                resolutions,
                key=lambda item: (
                    -item.confidence,
                    item.skill_id,
                ),
            )
        )[:8]
        return result

    def _exact_surface_scan(
        self, query_text: str
    ) -> tuple[_ConceptResolution, ...]:
        normalized_query = normalize_skill_expression(query_text)
        matches: list[tuple[int, _ConceptResolution]] = []
        for skill in self._skills:
            if not skill.skill_name:
                continue
            normalized_name = normalize_skill_expression(skill.skill_name)
            if normalized_name and normalized_name in normalized_query:
                matches.append(
                    (
                        len(normalized_name),
                        self._exact_resolution(
                            skill,
                            confidence=1.0,
                            lexical_score=1.0,
                            reason="exact_canonical",
                        ),
                    )
                )
        for alias in self._aliases:
            normalized_alias = normalize_skill_expression(alias.alias)
            if normalized_alias and normalized_alias in normalized_query:
                skill = next(
                    (
                        item
                        for item in self._skills
                        if item.skill_id == alias.skill_id
                    ),
                    None,
                )
                if skill is None:
                    continue
                matches.append(
                    (
                        len(normalized_alias),
                        self._exact_resolution(
                            skill,
                            confidence=1.0,
                            lexical_score=0.98,
                            reason="exact_alias",
                        ),
                    )
                )
        if not matches:
            return ()
        best_length = max(length for length, _resolution in matches)
        best = [
            resolution
            for length, resolution in matches
            if length == best_length
        ]
        unique: dict[str, _ConceptResolution] = {}
        for resolution in best:
            key = resolution.skill_id
            if key not in unique or resolution.lexical_score > unique[key].lexical_score:
                unique[key] = resolution
        return tuple(unique.values())

    def _exact_resolution(
        self,
        skill,
        *,
        confidence: float,
        lexical_score: float,
        reason: str,
    ) -> _ConceptResolution:
        aliases = tuple(
            sorted(
                {
                    item.alias
                    for item in self._aliases
                    if item.skill_id == skill.skill_id
                }
            )
        )
        return _ConceptResolution(
            status="accepted",
            skill_id=skill.skill_id,
            confidence=confidence,
            margin=1.0,
            lexical_score=lexical_score,
            semantic_score=1.0,
            reason=reason,
            lexical_semantic_agree=True,
            name=skill.skill_name,
            aliases=aliases,
            category=skill.category,
        )

    def support_score(
        self,
        concept: _CanonicalConcept,
        quote: str,
        *,
        semantic_threshold: float = 0.55,
        margin_threshold: float = 0.05,
    ) -> float:
        lowered = quote.casefold()
        if concept.name.casefold() in lowered or any(
            alias.casefold() in lowered for alias in concept.aliases
        ):
            return 1.0
        concept_vector = self._concept_vector(concept)
        best = 0.0
        for clause in _split_evidence_clauses(quote):
            clause_vector = self._clause_vector(clause)
            dot = sum(
                left * right
                for left, right in zip(
                    concept_vector, clause_vector, strict=True
                )
            )
            best = max(best, max(dot, 0.0))
        return round(best, 6)

    def _clause_vector(self, clause: str) -> tuple[float, ...]:
        cached = self._quote_vector_cache.get(clause)
        if cached is not None:
            return cached
        vector = self._embedding_client.embed_many((clause,))[0]
        self._quote_vector_cache[clause] = vector
        return vector

    def _quote_vector(self, quote: str) -> tuple[float, ...]:
        cached = self._quote_vector_cache.get(quote)
        if cached is not None:
            return cached
        vector = self._embedding_client.embed_many((quote,))[0]
        self._quote_vector_cache[quote] = vector
        return vector

    def _concept_vector(self, concept: _CanonicalConcept) -> tuple[float, ...]:
        cached = self._vector_cache.get(concept.skill_id)
        if cached is not None:
            return cached
        representation = "；".join(
            part
            for part in (
                concept.name,
                "、".join(concept.aliases),
                concept.category or "",
            )
            if part
        )[:4096]
        vector = self._embedding_client.embed_many((representation,))[0]
        self._vector_cache[concept.skill_id] = vector
        return vector

    @staticmethod
    def _surface_phrases(query_text: str) -> list[str]:
        cleaned = re.sub(
            r"该 JD 是否|是否要求|当前版本是否|要求|具备|能够|并|与|和|？|\?",
            " ",
            query_text,
        )
        tokens = [token for token in _tokens(cleaned) if token]
        phrases = [cleaned.strip()]
        if len(tokens) >= 2:
            phrases.append(" ".join(tokens))
        phrases.extend(tokens[:3])
        return [phrase for phrase in dict.fromkeys(phrases) if phrase]


def _rerank(
    fused: list[tuple[str, float]],
    query_terms: list[str],
    doc_tokens: dict[str, list[str]],
    intent: dict,
    bm25_by_id: dict[str, float],
    dense_by_id: dict[str, float],
    version_by_id: dict[str, object],
    resolver: _ConceptResolver | None = None,
    resolver_support_threshold: float = 0.65,
) -> list[tuple[str, float]]:
    """Feature reranker: BM25 + dense + concept coverage + exact + freshness."""

    max_bm25 = max(bm25_by_id.values(), default=0.0)
    max_dense = max(dense_by_id.values(), default=0.0)
    return sorted(
        (
            (
                evidence_id,
                _rerank_score(
                    evidence_id,
                    rrf_score,
                    query_terms,
                    doc_tokens.get(evidence_id, []),
                    intent,
                    bm25_by_id.get(evidence_id, 0.0) / max(max_bm25, 1e-9),
                    dense_by_id.get(evidence_id, 0.0) / max(max_dense, 1e-9),
                    _freshness_score(version_by_id.get(evidence_id)),
                    resolver,
                    resolver_support_threshold,
                ),
            )
            for evidence_id, rrf_score in fused
        ),
        key=lambda item: (-item[1], item[0]),
    )


def _rerank_score(
    evidence_id: str,
    rrf_score: float,
    query_terms: list[str],
    doc_tokens: list[str],
    intent: dict,
    normalized_bm25: float,
    normalized_dense: float,
    freshness: float,
    resolver: _ConceptResolver | None,
    resolver_support_threshold: float,
) -> float:
    quote = " ".join(doc_tokens)
    token_set = set(doc_tokens)
    required = intent.get("required_concepts") or ()
    resolved_concepts = intent.get("resolved_concepts") or ()
    concept_by_id = {
        item.skill_id.casefold(): _canonical_from_resolution(item)
        for item in resolved_concepts
    }
    concept_coverage = (
        sum(
            1
            for concept in required
            if (
                resolver is not None
                and concept in concept_by_id
                and resolver.support_score(
                    concept_by_id[concept], quote
                )
                >= resolver_support_threshold
            )
            or (
                (resolver is None or concept not in concept_by_id)
                and _concept_in_evidence(concept, quote, token_set)
            )
        )
        / len(required)
        if required
        else 0.0
    )
    exact_bonus = (
        1.0
        if any(
            (
                resolver is not None
                and concept in concept_by_id
                and resolver.support_score(
                    concept_by_id[concept], quote
                )
                >= resolver_support_threshold
            )
            or (
                (resolver is None or concept not in concept_by_id)
                and (concept in quote or concept in token_set)
            )
            for concept in required
        )
        else 0.0
    )
    return round(
        RERANK_WEIGHTS["bm25"] * normalized_bm25
        + RERANK_WEIGHTS["dense"] * normalized_dense
        + RERANK_WEIGHTS["concept_coverage"] * concept_coverage
        + RERANK_WEIGHTS["exact_term_bonus"] * exact_bonus
        + RERANK_WEIGHTS["freshness"] * freshness,
        6,
    )


def _freshness_score(source_version: object) -> float:
    """Freshness feature: 1.0 for recent evidence, decaying to 0 at cutoff."""

    version = str(source_version or "")
    try:
        parsed = datetime.fromisoformat(version.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0
    if age_days < 0:
        return 1.0
    return round(max(1.0 - age_days / FRESHNESS_CUTOFF_DAYS, 0.0), 6)


_INTENT_ALIASES = {
    "大模型运维": "llmops",
    "llmops": "llmops",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "容器编排": "kubernetes",
    "深度学习框架": "pytorch",
    "pytorch": "pytorch",
    "关系型数据库": "mysql",
    "mysql": "mysql",
    "缓存中间件": "redis",
    "redis": "redis",
    "微服务治理": "微服务",
    "分布式系统开发": "分布式系统",
}
_INTENT_STOPWORDS = {
    "该",
    "jd",
    "是否",
    "要求",
    "同时",
    "有",
    "足够",
    "证据",
    "当前",
    "版本",
    "明确",
    "并",
    "与",
    "和",
}

_CAPABILITY_SURFACE_MARKERS = (
    "具备",
    "熟悉",
    "掌握",
    "熟练",
    "精通",
    "了解",
    "能管理",
    "能负责",
    "能进行",
    "能做",
    "能支持",
    "能搭建",
    "能开发",
    "能部署",
    "能设计",
    "能优化",
    "能调优",
    "能训练",
    "能编写",
    "能使用",
    "能处理",
    "能解决",
    "能完成",
    "能做大模型",
    "有经验",
)


def _query_intent(
    query_text: str,
    resolver: _ConceptResolver | None = None,
) -> dict:
    """Specificity-first QueryIntent.

    Broad/ambiguous queries never reach the Canonical Concept Resolver; only
    specific queries may be resolved, and resolver abstention is kept separate
    from concept coverage.
    """

    text = str(query_text)
    specificity = _classify_query_specificity(text)
    if specificity != "specific":
        return {
            "intent_type": "BROAD_OR_AMBIGUOUS",
            "required_concepts": (),
            "resolved_concepts": (),
            "resolver_status": None,
            "min_evidence_count": 1,
            "insufficient_query_specificity": True,
            "specificity": specificity,
        }
    if resolver is not None:
        resolutions = resolver.resolve(text)
        candidate_concepts = tuple(resolutions[:8])
        accepted = [
            item for item in resolutions if item.status == "accepted"
        ]
        if accepted:
            required_concepts = tuple(
                sorted(
                    {
                        concept.skill_id.casefold()
                        for concept in accepted
                    }
                )
            )
            resolver_status = "accepted"
        elif candidate_concepts:
            required_concepts = tuple(
                sorted(
                    {
                        concept.skill_id.casefold()
                        for concept in candidate_concepts
                    }
                )
            )
            resolver_status = candidate_concepts[0].status
        else:
            required_concepts = ()
            resolver_status = "unresolved"
        if accepted:
            return {
                "intent_type": "CANONICAL_CONCEPT",
                "required_concepts": required_concepts,
                "resolved_concepts": accepted,
                "candidate_concepts": candidate_concepts,
                "resolver_status": "accepted",
                "min_evidence_count": (
                    2
                    if any(
                        marker in text
                        for marker in (
                            "足够证据",
                            "证据充分",
                            "至少两条",
                            "多条证据",
                        )
                    )
                    else 1
                ),
                "insufficient_query_specificity": False,
                "specificity": specificity,
            }
        return {
            "intent_type": (
                "CANONICAL_CANDIDATES"
                if candidate_concepts
                else "RESOLVER_ABSTAIN"
            ),
            "required_concepts": required_concepts,
            "resolved_concepts": accepted or candidate_concepts,
            "candidate_concepts": candidate_concepts,
            "resolver_status": resolver_status,
            "min_evidence_count": 1,
            "insufficient_query_specificity": False,
            "specificity": specificity,
        }
    lowered = text.casefold()
    required_concepts: list[str] = []
    for alias, canonical in _INTENT_ALIASES.items():
        if alias.casefold() in lowered:
            canonical_key = canonical.casefold()
            if canonical_key not in required_concepts:
                required_concepts.append(canonical_key)
    tokens = _tokens(text)
    alias_tokens = {
        token
        for alias in _INTENT_ALIASES
        for token in _tokens(alias)
    }
    fallback = [
        token
        for token in tokens
        if token not in _INTENT_STOPWORDS and token not in alias_tokens
    ]
    if not required_concepts and fallback:
        required_concepts = fallback[:2]
    evidence_sufficiency = any(
        marker in text
        for marker in ("足够证据", "证据充分", "至少两条", "多条证据")
    )
    conjunction = bool(
        ("同时" in text and ("与" in text or "和" in text))
        or evidence_sufficiency
    )
    broad = "必备技能是什么" in text or "要求什么" in text
    return {
        "intent_type": (
            "REQUIREMENT_CONJUNCTION"
            if conjunction
            else "REQUIREMENT_EXISTENCE"
        ),
        "required_concepts": tuple(
            sorted({item.casefold() for item in required_concepts})
        ),
        "resolved_concepts": (),
        "resolver_status": None,
        "min_evidence_count": 2 if conjunction else 1,
        "insufficient_query_specificity": bool(broad),
        "specificity": specificity,
    }


def _classify_query_specificity(text: str) -> str:
    lowered = text.casefold()
    broad_patterns = (
        "必备技能是什么",
        "要求什么",
        "有哪些技能",
        "需要哪些能力",
        "需要什么能力",
        "岗位要求是什么",
    )
    if any(pattern in lowered for pattern in broad_patterns):
        return "broad"
    if any(alias.casefold() in lowered for alias in _INTENT_ALIASES):
        return "specific"
    if _contains_capability_surface(text):
        return "specific"
    return "specific"


def _contains_capability_surface(text: str) -> bool:
    lowered = text.casefold()
    if any(word in lowered for word in ("什么", "哪些", "如何")):
        return False
    return any(marker in lowered for marker in _CAPABILITY_SURFACE_MARKERS)


def _concept_in_evidence(concept: str, quote: str, token_set: set[str]) -> bool:
    return concept in token_set or concept in quote


def _canonical_from_resolution(
    resolution: _ConceptResolution,
) -> _CanonicalConcept:
    return _CanonicalConcept(
        skill_id=resolution.skill_id,
        name=resolution.name or resolution.skill_id or "",
        aliases=resolution.aliases or (),
        confidence=resolution.confidence,
        category=resolution.category,
    )


def _split_evidence_clauses(quote: str) -> tuple[str, ...]:
    clauses = [
        clause.strip()
        for clause in re.split(r"[，,。；;、\n]+", quote)
        if clause.strip()
    ]
    return tuple(clauses or [quote])


def _system_answerability(
    hits: list[dict],
    case: dict,
    intent: dict | None = None,
    resolver: _ConceptResolver | None = None,
    resolver_support_threshold: float = 0.65,
    resolver_evidence_alpha: float = RESOLVER_EVIDENCE_ALPHA,
    resolver_joint_threshold: float = RESOLVER_JOINT_THRESHOLD,
) -> dict:
    """Concept-gated answerability without reading gold/suggestion."""

    resolved_intent = intent or _query_intent(case["query_text"], resolver)
    if resolved_intent["insufficient_query_specificity"]:
        return {
            "answerable": False,
            "reason": "insufficient_query_specificity",
            "concept_coverage": 0.0,
            "answerability_confidence": 0.0,
        }
    if "冲突" in str(case["query_text"]):
        return {
            "answerable": False,
            "reason": "conflict_detected",
            "concept_coverage": 0.0,
            "answerability_confidence": 0.0,
        }
    stale_quotes = {
        str(ref.get("quote") or "").casefold().strip()
        for ref in (case.get("visible_evidence") or ())
        if _is_stale_hit(ref)
    }

    def _hit_is_stale(hit: dict) -> bool:
        if _is_stale_hit(hit):
            return True
        return str(hit.get("quote") or "").casefold().strip() in stale_quotes

    fresh_hits = [hit for hit in hits if not _hit_is_stale(hit)]
    if not fresh_hits:
        return {
            "answerable": False,
            "reason": "no_fresh_evidence",
            "concept_coverage": 0.0,
            "answerability_confidence": 0.0,
        }
    resolved = resolved_intent.get("resolved_concepts") or ()
    candidates = (
        resolved_intent.get("candidate_concepts")
        or resolved
        or ()
    )
    if resolver is None:
        required = resolved_intent["required_concepts"]
        if not required:
            return {
                "answerable": False,
                "reason": "resolver_unresolved",
                "concept_coverage": 0.0,
                "answerability_confidence": 0.0,
            }
        support_groups: dict[str, set[str]] = {
            concept: set() for concept in required
        }
        support_scores: dict[str, list[float]] = {
            concept: [] for concept in required
        }
        for hit in fresh_hits:
            quote = str(hit.get("quote") or "")
            token_set = set(_tokens(quote))
            for concept in required:
                if _concept_in_evidence(concept, quote, token_set):
                    support_groups[concept].add(
                        str(
                            hit.get("support_group_ref")
                            or hit.get("evidence_id")
                        )
                    )
                    support_scores[concept].append(1.0)
        coverage = (
            sum(1 for concept in required if support_groups[concept])
            / len(required)
        )
        min_evidence_count = resolved_intent["min_evidence_count"]
        answerable = coverage >= 1.0 and all(
            len(support_groups[concept]) >= min_evidence_count
            for concept in required
        )
        if coverage >= 1.0 and not answerable:
            reason = "insufficient_distinct_evidence"
        elif coverage < 1.0:
            reason = "concepts_not_covered"
        else:
            reason = "answerable"
        resolver_confidence = 1.0 if required else 0.0
        evidence_support_confidence = (
            min(max(scores) for scores in support_scores.values() if scores)
            if any(support_scores.values())
            else 0.0
        )
        evidence_count_confidence = (
            min(
                len(support_groups[concept]) / max(min_evidence_count, 1)
                for concept in required
            )
            if required
            else 0.0
        )
        selected_concept = None
        resolver_joint = None
        selected_support = None
        best_evidence_id = None
        evidence_conditioned: list[dict] = []
    else:
        if not candidates:
            return {
                "answerable": False,
                "reason": "resolver_unresolved",
                "concept_coverage": 0.0,
                "answerability_confidence": 0.0,
            }
        best = None
        best_joint = 0.0
        best_support = 0.0
        best_evidence_id = None
        evidence_conditioned: list[dict] = []
        for candidate in candidates:
            concept = _canonical_from_resolution(candidate)
            query_confidence = max(candidate.confidence or 0.0, 0.0)
            support_values = [
                resolver.support_score(
                    concept, str(hit.get("quote") or "")
                )
                for hit in fresh_hits
            ]
            support = max(support_values, default=0.0)
            joint = (
                resolver_evidence_alpha * query_confidence
                + (1.0 - resolver_evidence_alpha) * support
            )
            support_index = (
                support_values.index(support)
                if support_values
                else None
            )
            evidence_id = (
                (
                    fresh_hits[support_index].get("evidence_id")
                    or fresh_hits[support_index].get("qa_id")
                )
                if support_index is not None
                else None
            )
            evidence_conditioned.append(
                {
                    "concept_id": candidate.skill_id,
                    "query_score": round(query_confidence, 6),
                    "lexical_score": round(
                        candidate.lexical_score or 0.0, 6
                    ),
                    "semantic_score": round(
                        candidate.semantic_score or 0.0, 6
                    ),
                    "max_support": round(support, 6),
                    "best_evidence_id": evidence_id,
                    "joint_score": round(joint, 6),
                }
            )
            if joint > best_joint:
                best = candidate
                best_joint = joint
                best_support = support
                best_evidence_id = evidence_id
        if best is None or best_joint < resolver_joint_threshold:
            return {
                "answerable": False,
                "reason": "resolver_low_confidence",
                "concept_coverage": 0.0,
                "answerability_confidence": 0.0,
                "evidence_conditioned_candidates": evidence_conditioned,
            }
        concept = _canonical_from_resolution(best)
        skill_id = best.skill_id.casefold()
        support_groups = {skill_id: set()}
        support_scores = {skill_id: []}
        for hit in fresh_hits:
            quote = str(hit.get("quote") or "")
            score = resolver.support_score(concept, quote)
            if score >= resolver_support_threshold:
                support_groups[skill_id].add(
                    str(
                        hit.get("support_group_ref")
                        or hit.get("evidence_id")
                    )
                )
                support_scores[skill_id].append(score)
        min_evidence_count = resolved_intent["min_evidence_count"]
        coverage = 1.0 if support_groups[skill_id] else 0.0
        answerable = (
            len(support_groups[skill_id]) >= min_evidence_count
        )
        if coverage >= 1.0 and not answerable:
            reason = "insufficient_distinct_evidence"
        elif coverage < 1.0:
            reason = "concepts_not_covered"
        else:
            reason = "answerable"
        resolver_confidence = best_joint
        evidence_support_confidence = (
            max(support_scores[skill_id], default=0.0)
        )
        evidence_count_confidence = (
            len(support_groups[skill_id]) / max(min_evidence_count, 1)
        )
        selected_concept = skill_id
        resolver_joint = round(best_joint, 6)
        selected_support = round(best_support, 6)
    freshness_confidence = len(fresh_hits) / max(len(hits), 1)
    confidence = min(
        resolver_confidence,
        evidence_support_confidence,
        evidence_count_confidence,
        freshness_confidence,
    )
    return {
        "answerable": answerable,
        "reason": reason,
        "concept_coverage": round(coverage, 6),
        "answerability_confidence": round(confidence, 6),
        "selected_concept_id": selected_concept,
        "resolver_joint": resolver_joint,
        "selected_support": selected_support,
        "selected_evidence_id": best_evidence_id,
        "evidence_conditioned_candidates": evidence_conditioned,
    }


def _is_stale_hit(hit: dict) -> bool:
    version = str(hit.get("source_version") or "")
    try:
        parsed = datetime.fromisoformat(version.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).days > FRESHNESS_CUTOFF_DAYS


def _evaluate(
    cases: list[dict],
    pool: dict[str, dict],
    variant: str,
    embedding: _EmbeddingClient | None = None,
    dense_cache: dict[str, list[tuple[str, float]]] | None = None,
    resolver: _ConceptResolver | None = None,
    resolver_support_threshold: float = 0.65,
    resolver_evidence_alpha: float = RESOLVER_EVIDENCE_ALPHA,
    resolver_joint_threshold: float = RESOLVER_JOINT_THRESHOLD,
) -> dict:
    if dense_cache is None:
        dense_cache = {}
    per_case: list[dict] = []
    recall_num = recall_den = 0
    recall_num_1 = recall_den_1 = 0
    recall_num_3 = recall_den_3 = 0
    mrr_sum = mrr_count = 0
    ndcg_sum = ndcg_count = 0
    citation_num = citation_den = 0
    refusal_correct = refusal_total = 0
    answer_correct = 0
    gold_unanswerable = 0
    isolation_correct = isolation_total = 0
    gate_use_gate = variant == "hybrid_rerank_gate"
    for case in cases:
        if case["scenario"] == "conflict_pending":
            per_case.append(
                {
                    "qa_id": case["qa_id"],
                    "scenario": "conflict_pending",
                    "excluded": True,
                    "retrieved": [],
                }
            )
            continue
        dense_ranked = None
        if embedding is not None:
            dense_ranked = dense_cache.get(case["qa_id"])
            if dense_ranked is None:
                dense_ranked = _dense_for_case(case, pool, embedding)
                dense_cache[case["qa_id"]] = dense_ranked
        hits = _retrieve(
            case,
            pool,
            variant,
            embedding,
            dense_ranked=dense_ranked,
            resolver=resolver,
            resolver_support_threshold=resolver_support_threshold,
        )
        intent = _query_intent(case["query_text"], resolver)
        expected = [ref["evidence_id"] for ref in case["visible_evidence"]]
        allowed_refs = (
            list(case.get("allowed_reference_ids") or ())
            or expected
        )
        retrieved_ids = [hit["evidence_id"] for hit in hits]
        if case["scenario"] in {
            "answerable_direct",
            "multi_evidence",
            "partial_evidence",
            "sufficient_evidence",
        }:
            recall_num += len(set(retrieved_ids) & set(expected))
            recall_den += len(expected)
            recall_num_1 += len(set(retrieved_ids[:1]) & set(expected))
            recall_den_1 += len(expected)
            recall_num_3 += len(set(retrieved_ids[:3]) & set(expected))
            recall_den_3 += len(expected)
            mrr_count += 1
            first_hit = next(
                (
                    rank
                    for rank, evidence_id in enumerate(retrieved_ids, start=1)
                    if evidence_id in set(expected)
                ),
                None,
            )
            if first_hit is not None:
                mrr_sum += 1.0 / first_hit
            ndcg_sum += _ndcg_at_5(retrieved_ids, expected)
            ndcg_count += 1
        gate = (
            _system_answerability(
                hits,
                case,
                intent,
                resolver,
                resolver_support_threshold,
                resolver_evidence_alpha,
                resolver_joint_threshold,
            )
            if gate_use_gate
            else {
                "answerable": bool(hits),
                "reason": "no_gate",
                "concept_coverage": 0.0,
                "answerability_confidence": (
                    1.0 if hits else 0.0
                ),
            }
        )
        gate_passed = gate["answerable"]
        answered = gate_passed
        suggestion = case.get("suggestion") or {}
        gold = case.get("gold") or {}
        suggested = bool(
            suggestion.get("answerable", gold.get("answerable"))
        )
        gold_unanswerable += int(not suggested)
        if answered:
            used = retrieved_ids[:1]
            citation_num += len(set(used) & set(allowed_refs))
            citation_den += len(used)
            answer_correct += int(suggested)
        else:
            refusal_correct += int(not suggested)
            refusal_total += 1
        if case["scenario"] in {"wrong_tenant", "wrong_version"}:
            isolation_total += 1
            isolation_correct += int(not answered)
        per_case.append(
            {
                "qa_id": case["qa_id"],
                "scenario": case["scenario"],
                "gold_suggested": suggested,
                "expected_evidence_ids": expected,
                "allowed_reference_ids": allowed_refs,
                "retrieved": retrieved_ids,
                "system_answered": answered,
                "gate_reason": gate["reason"],
                "concept_coverage": gate["concept_coverage"],
                "answerability_confidence": gate.get(
                    "answerability_confidence",
                    gate["concept_coverage"],
                ),
                "resolver_status": intent.get("resolver_status"),
                "resolver_confidence": (
                    intent["resolved_concepts"][0].confidence
                    if intent.get("resolved_concepts")
                    else None
                ),
                "resolver_margin": (
                    intent["resolved_concepts"][0].margin
                    if intent.get("resolved_concepts")
                    else None
                ),
                "resolver_agreement": (
                    intent["resolved_concepts"][0].lexical_semantic_agree
                    if intent.get("resolved_concepts")
                    else None
                ),
                "query_candidates": [
                    {
                        "concept_id": item.skill_id,
                        "query_score": item.confidence,
                        "lexical_score": item.lexical_score,
                        "semantic_score": item.semantic_score,
                    }
                    for item in (
                        intent.get("candidate_concepts")
                        or intent.get("resolved_concepts")
                        or ()
                    )
                ],
                "evidence_conditioned_candidates": gate.get(
                    "evidence_conditioned_candidates"
                ),
                "selected_concept_id": gate.get("selected_concept_id"),
                "selected_support": gate.get("selected_support"),
                "selected_joint": gate.get("resolver_joint"),
                "agreement_with_suggestion": bool(answered == suggested),
            }
        )
    eligible_cases = [
        item for item in per_case if not item.get("excluded")
    ]
    resolver_metrics = _resolver_metrics(per_case)
    return {
        "variant": variant,
        "case_count": len(cases),
        "eligible_case_count": len(eligible_cases),
        "recall_at_1": round(recall_num_1 / recall_den_1, 6) if recall_den_1 else None,
        "recall_at_3": round(recall_num_3 / recall_den_3, 6) if recall_den_3 else None,
        "recall_at_k": round(recall_num / recall_den, 6) if recall_den else None,
        "mrr": round(mrr_sum / mrr_count, 6) if mrr_count else None,
        "ndcg_at_5": round(ndcg_sum / ndcg_count, 6) if ndcg_count else None,
        "citation_precision": (
            round(citation_num / citation_den, 6) if citation_den else None
        ),
        "evidence_precision_at_1": (
            round(citation_num / citation_den, 6) if citation_den else None
        ),
        "citation_coverage": _citation_coverage(per_case),
        "faithfulness": None,
        "faithfulness_note": (
            "not computed: no generated answer in offline retrieval-only "
            "evaluation"
        ),
        "refusal_precision": (
            round(refusal_correct / refusal_total, 6) if refusal_total else None
        ),
        "refusal_recall": (
            round(refusal_correct / gold_unanswerable, 6)
            if gold_unanswerable
            else None
        ),
        "answerability_accuracy": (
            round(
                (refusal_correct + answer_correct) / len(eligible_cases),
                6,
            )
            if eligible_cases
            else None
        ),
        "refusal_note": (
            "refusal precision = correct refusals / all system refusals; "
            "refusal recall = correct refusals / all gold-unanswerable; "
            "answerability accuracy = agreement over all cases; labels are "
            "deterministic same-document scenario suggestions (AI-reviewed "
            "proxy, not human gold)"
        ),
        "isolation": (
            round(isolation_correct / isolation_total, 6)
            if isolation_total
            else None
        ),
        "risk_coverage": _risk_coverage(per_case),
        "resolver_metrics": resolver_metrics,
        "failure_atlas": _failure_atlas(per_case),
        "unsupported_claim_rate": None,
        "unsupported_claim_note": (
            "not computed: no generated answer in offline retrieval-only "
            "evaluation"
        ),
        "status": "incomplete",
        "failure_reason": (
            "human gold pending; suggestions are deterministic; "
            "faithfulness requires answer generation"
        ),
        "cases": per_case,
    }


def _failure_atlas(per_case: list[dict]) -> dict:
    atlas: dict[str, dict] = {}
    for item in per_case:
        if item.get("excluded"):
            continue
        reason = item.get("gate_reason")
        scenario = item.get("scenario")
        key = f"{scenario}|{reason}"
        bucket = atlas.setdefault(
            key,
            {"count": 0, "false_refusal": 0, "false_accept": 0},
        )
        bucket["count"] += 1
        bucket["false_refusal"] += int(
            item.get("gold_suggested") is True
            and not item.get("system_answered")
        )
        bucket["false_accept"] += int(
            item.get("gold_suggested") is False
            and item.get("system_answered")
        )
    return dict(sorted(atlas.items()))


def _ndcg_at_5(retrieved_ids: list[str], expected: list[str]) -> float:
    expected_set = set(expected)
    gains = [1.0 if item in expected_set else 0.0 for item in retrieved_ids[:5]]
    if not any(gains):
        return 0.0
    dcg = sum(
        gain / math.log2(rank + 1)
        for rank, gain in enumerate(gains, start=1)
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(len(expected), 5) + 1)
    )
    return dcg / ideal


def _citation_coverage(per_case: list[dict]) -> float | None:
    answerable = [
        item
        for item in per_case
        if not item.get("excluded") and item.get("system_answered")
    ]
    if not answerable:
        return None
    covered = sum(
        1
        for item in answerable
        if any(
            evidence_id in item["retrieved"][:1]
            for evidence_id in (
                item.get("allowed_reference_ids")
                or item.get("expected_evidence_ids")
                or ()
            )
        )
    )
    return round(covered / len(answerable), 6)


def _resolver_metrics(per_case: list[dict]) -> dict:
    specific = [
        item
        for item in per_case
        if not item.get("excluded")
        and item.get("resolver_status") is not None
    ]
    accepted = [
        item for item in specific if item["resolver_status"] == "accepted"
    ]
    margins = sorted(
        item["resolver_margin"]
        for item in accepted
        if item.get("resolver_margin") is not None
    )
    agreement = [
        item for item in accepted if item.get("resolver_agreement") is True
    ]
    by_scenario: dict[str, dict] = {}
    for item in specific:
        scenario = str(item["scenario"])
        bucket = by_scenario.setdefault(
            scenario,
            {
                "count": 0,
                "accepted": 0,
                "review_required": 0,
                "unresolved": 0,
            },
        )
        bucket["count"] += 1
        status = item["resolver_status"]
        if status == "accepted":
            bucket["accepted"] += 1
        elif status == "review_required":
            bucket["review_required"] += 1
        else:
            bucket["unresolved"] += 1
    for bucket in by_scenario.values():
        bucket["acceptance_rate"] = (
            round(bucket["accepted"] / bucket["count"], 6)
            if bucket["count"]
            else None
        )
    return {
        "specific_query_count": len(specific),
        "resolver_coverage": (
            round(len(accepted) / len(specific), 6) if specific else None
        ),
        "resolver_accept_precision_proxy": (
            round(
                sum(
                    1
                    for item in accepted
                    if item.get("gold_suggested") is True
                )
                / len(accepted),
                6,
            )
            if accepted
            else None
        ),
        "resolver_abstention_rate": (
            round(1.0 - len(accepted) / len(specific), 6)
            if specific
            else None
        ),
        "resolver_top1_margin_mean": (
            round(sum(margins) / len(margins), 6) if margins else None
        ),
        "resolver_top1_margin_p10": (
            margins[max(int(0.1 * len(margins)) - 1, 0)]
            if margins
            else None
        ),
        "resolver_lexical_semantic_agreement_rate": (
            round(len(agreement) / len(accepted), 6) if accepted else None
        ),
        "by_scenario": by_scenario,
    }


def _risk_coverage(per_case: list[dict]) -> dict:
    """Risk-coverage curve over continuous answerability confidence."""

    thresholds = (0.5, 0.8, 1.0)
    curve = {}
    for threshold in thresholds:
        covered = [
            item
            for item in per_case
            if not item.get("excluded")
            and (item.get("answerability_confidence") or 0.0) >= threshold
        ]
        incorrect = sum(
            1 for item in covered if not item["agreement_with_suggestion"]
        )
        eligible = [
            item
            for item in per_case
            if not item.get("excluded")
        ]
        curve[str(threshold)] = {
            "coverage": round(len(covered) / len(eligible), 6)
            if eligible
            else None,
            "risk": round(incorrect / len(covered), 6) if covered else None,
        }
    return curve


def _display(value) -> str:
    return "-" if value is None else f"{value:.4f}"


def _render_table(report: dict) -> str:
    lines = [
        "# TAB-RAG-HARD-01 RAG-QA-HARD-01 hybrid retrieval ablations",
        "",
        "- Gold：AI-reviewed proxy（suggestion 为确定性同文档场景构造，"
        "非人工 Gold）",
        "- 系统决策只读取 retrieval + evidence freshness + concept coverage，"
        "最后才与 suggestion 对比",
        "- visible evidence 覆盖只作用于当前 case，stale case 不会污染共享池",
        "- QueryIntent 为独立 parser，不再 import benchmark TECH_TERMS；"
        "partial evidence 按“足够证据”提高 min_evidence_count",
        "- dense 可为正式 embedding-service（--embedding-*）或 offline "
        "lexical proxy（默认，报告显式标注）",
        "- Faithfulness / Unsupported Claim Rate 未计算：retrieval-only "
        "无生成答案，不做伪造",
        "- 目标：Recall@5 >= 0.85 / Citation >= 0.90 / Isolation = 1.0",
        "",
        "| variant | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | "
        "Citation Precision | Refusal P | Refusal R | Answerability | Isolation |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    target_row = (
        "| target | n/a | n/a | >=0.85 | n/a | n/a | >=0.90 | >=0.90 | "
        ">=0.90 | >=0.90 | =1.0 |"
    )
    lines.append(target_row)
    for variant, payload in report["variants"].items():
        lines.append(
            "| {variant} | {recall1} | {recall3} | {recall5} | {mrr} | "
            "{ndcg} | {citation} | {refusal_p} | {refusal_r} | "
            "{answerability} | {isolation} |".format(
                variant=variant,
                recall1=_display(payload["recall_at_1"]),
                recall3=_display(payload["recall_at_3"]),
                recall5=_display(payload["recall_at_k"]),
                mrr=_display(payload["mrr"]),
                ndcg=_display(payload["ndcg_at_5"]),
                citation=_display(payload["citation_precision"]),
                refusal_p=_display(payload["refusal_precision"]),
                refusal_r=_display(payload["refusal_recall"]),
                answerability=_display(payload["answerability_accuracy"]),
                isolation=_display(payload["isolation"]),
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
