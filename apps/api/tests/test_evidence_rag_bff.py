from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

import pytest
from pydantic import ValidationError

from app.contexts.evidence_rag import (
    EvidenceCitationTarget,
    EvidenceRagError,
    EvidenceRagHit,
    ManageEvidenceRag,
)
from app.contexts.evidence_rag.contracts import (
    EvidenceEntailment,
    EvidenceRagLlmAnswer,
)
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.json_types import freeze_json_object
from app.infrastructure.evidence_rag_store import QdrantEvidenceRagStore
from app.schemas.evidence_rag import EvidenceCitationResolution, EvidenceRagBFFRequest


ADMIN = AccountActor(account_id="account-1", role="admin")
PERSONAL = AccountActor(account_id="account-2", role="personal_user")
PLATFORM_PERMISSION = ("jobgraph-platform-public", "platform:public")


def bff_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": "evidence-rag-query.v1",
        "business_object": {
            "object_type": "cv_profile",
            "object_id": "profile-1",
            "object_version": "v1",
        },
        "query_text": "是否具备 RAG 项目经验",
        "evidence_types": ["cv_evidence"],
        "graph_version_id": 7,
    }
    payload.update(overrides)
    return payload


def hit(
    evidence_id: str = "evidence-1",
    *,
    score: float = 0.91,
    quote: str = "负责 RAG 应用开发",
    business_object_id: str = "profile-1",
    graph_version_id: int = 7,
    business_object_name: str | None = None,
) -> EvidenceRagHit:
    return EvidenceRagHit(
        evidence_id=evidence_id,
        source_object_type="validated_cv_snapshot",
        source_object_id="snapshot-1",
        source_document_id="document-1",
        source_version="v1",
        score=score,
        quote=quote,
        location_start=0,
        location_end=9,
        occurrence_index=0,
        alignment="exact",
        graph_version_id=graph_version_id,
        graph_version=None,
        business_version=None,
        tenant_ref="jobgraph-platform-public",
        permission_scope="platform:public",
        business_object_id=business_object_id,
        business_object_name=business_object_name,
    )


class FakeEmbedding:
    def __init__(self, error: EvidenceRagError | None = None) -> None:
        self.error = error
        self.last_text = ""

    def embed(self, text: str) -> list[float]:
        if self.error is not None:
            raise self.error
        self.last_text = text
        return [0.1, 0.2]

    def embed_many(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        if self.error is not None:
            raise self.error
        return tuple((0.1, 0.2) for _ in texts)

    def model_id(self) -> str:
        return "BAAI/bge-m3"

    def model_revision(self) -> str:
        return "5617a9f61b028005a4858fdac845db406aefb181"

    def dimension(self) -> int:
        return 2


class FakeStore:
    def __init__(
        self,
        hits: tuple[EvidenceRagHit, ...] = (),
        error: EvidenceRagError | None = None,
        filter_multi_object_calls: bool = False,
        respect_query_top_k: bool = False,
    ) -> None:
        self.hits = hits
        self.error = error
        self.filter_multi_object_calls = filter_multi_object_calls
        self.respect_query_top_k = respect_query_top_k
        self.upserted: list[tuple[Any, list[float]]] = []
        self.upsert_many_calls: list[
            tuple[tuple[Any, ...], tuple[tuple[float, ...], ...]]
        ] = []
        self.count_value = 0
        self.count_calls: list[dict[str, object]] = []
        self.invalidated: list[dict[str, object]] = []
        self.deleted: list[dict[str, object]] = []
        self.last_query: Any | None = None
        self.search_queries: list[Any] = []

    def upsert(self, record: Any, vector: list[float]) -> None:
        if self.error is not None:
            raise self.error
        self.upserted.append((record, vector))

    def upsert_many(
        self,
        records: tuple[Any, ...],
        vectors: tuple[tuple[float, ...], ...],
    ) -> None:
        if self.error is not None:
            raise self.error
        self.upsert_many_calls.append((records, vectors))
        self.upserted.extend(
            (record, list(vector))
            for record, vector in zip(records, vectors)
        )

    def count(self, **filters: object) -> int:
        if self.error is not None:
            raise self.error
        self.count_calls.append(dict(filters))
        return self.count_value

    def search(
        self, query: Any, vector: list[float]
    ) -> tuple[EvidenceRagHit, ...]:
        if self.error is not None:
            raise self.error
        self.last_query = query
        self.search_queries.append(query)
        hits = self.hits
        if self.filter_multi_object_calls and query.version_scope == "single_object":
            hits = tuple(
                item
                for item in hits
                if item.business_object_id == query.business_object_id
                and item.graph_version_id == query.graph_version_id
            )
        if self.respect_query_top_k:
            hits = tuple(
                sorted(hits, key=lambda item: (-item.score, item.evidence_id))[
                    : query.top_k
                ]
            )
        return hits

    def citations(self, query: Any) -> tuple[EvidenceRagHit, ...]:
        if self.error is not None:
            raise self.error
        return tuple(
            item
            for item in self.hits
            if item.evidence_id == query.evidence_id
        )

    def deactivate(self, **filters: object) -> None:
        self.invalidated.append(dict(filters))

    def delete(self, **filters: object) -> None:
        self.deleted.append(dict(filters))


class FakeLlm:
    def __init__(
        self,
        error: EvidenceRagError | None = None,
        answer: EvidenceRagLlmAnswer | None = None,
        entailments: tuple[EvidenceEntailment, ...] | None = None,
    ) -> None:
        self.error = error
        self.llm_answer = answer or EvidenceRagLlmAnswer(
            answer="候选人具备 RAG 项目经验。",
            supported=True,
            used_evidence_ids=("evidence-1",),
        )
        self.llm_entailments = entailments or (
            EvidenceEntailment(
                claim="候选人具备 RAG 项目经验",
                relation="support",
                used_evidence_ids=("evidence-1",),
                reason="Evidence 直接支持该命题",
                dimension="skill",
            ),
        )
        self.last_evidence_text = ""
        self.last_query_depth = "fact"
        self.last_business_object_ids: tuple[str, ...] = ()
        self.last_objects_with_evidence: tuple[str, ...] = ()

    def entailment(
        self, *, query_text: str, evidence_text: str
    ) -> tuple[EvidenceEntailment, ...]:
        if self.error is not None:
            raise self.error
        return self.llm_entailments

    def judge_and_answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        query_depth: str = "fact",
        business_object_ids: tuple[str, ...] = (),
        objects_with_evidence: tuple[str, ...] = (),
    ) -> tuple[tuple[EvidenceEntailment, ...], EvidenceRagLlmAnswer]:
        if self.error is not None:
            raise self.error
        self.last_evidence_text = evidence_text
        self.last_query_depth = query_depth
        self.last_business_object_ids = business_object_ids
        self.last_objects_with_evidence = objects_with_evidence
        return self.llm_entailments, self.llm_answer

    def answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        entailment_text: str | None = None,
    ) -> EvidenceRagLlmAnswer:
        if self.error is not None:
            raise self.error
        self.last_evidence_text = evidence_text
        return self.llm_answer

    def provider(self) -> str:
        return "deepseek"

    def model(self) -> str:
        return "deepseek-v4-flash"

    def model_version(self) -> str:
        return "deepseek-evidence-rag-answer.v1"


class FakeCitationTargetResolver:
    routes = {
        "validated_cv_snapshot": "/profile/resumes?resumeId=resume-1",
        "published_jd_fact": "/data/jds?jdId=jd-1",
        "position_profile": "/positions/position-1",
        "matching_evidence": "/matching/reports/evaluation-1",
    }

    def resolve(self, *, actor_id: str, actor_role: str, hit: EvidenceRagHit):
        route = self.routes.get(hit.source_object_type)
        if route is None:
            raise EvidenceRagError("CITATION_SOURCE_UNSUPPORTED", "unsupported")
        resource_id = {
            "validated_cv_snapshot": "resume-1",
            "published_jd_fact": "jd-1",
            "position_profile": "position-1",
            "matching_evidence": "evaluation-1",
        }[hit.source_object_type]
        return EvidenceCitationTarget(route=route, resource_id=resource_id)


def handlers(
    *,
    embedding: FakeEmbedding | None = None,
    store: FakeStore | None = None,
    llm: FakeLlm | None = None,
    enabled: bool = True,
    resolver=lambda actor: PLATFORM_PERMISSION,
    max_context_chars: int = 8000,
    multi_object_max_hits: int = 40,
    multi_object_max_context_chars: int = 24000,
    min_score: float = 0.0,
    profile_text_provider=None,
    index_status_provider=None,
    citation_target_resolver=None,
) -> ManageEvidenceRag:
    return ManageEvidenceRag(
        embedding=embedding or FakeEmbedding(),
        store=store or FakeStore(),
        llm=llm or FakeLlm(),
        enabled=enabled,
        permission_resolver=resolver,
        profile_text_provider=profile_text_provider,
        index_status_provider=index_status_provider,
        top_k=5,
        max_context_chars=max_context_chars,
        multi_object_max_hits=multi_object_max_hits,
        multi_object_max_context_chars=multi_object_max_context_chars,
        min_score=min_score,
        citation_target_resolver=(
            citation_target_resolver or FakeCitationTargetResolver()
        ),
    )


def test_query_answered_with_server_assembled_permission_and_references():
    result = handlers(store=FakeStore(hits=(hit(),))).query(
        ADMIN, bff_payload()
    )

    assert result.status == "answered"
    assert result.answer == "候选人具备 RAG 项目经验。"
    assert result.supported is True
    assert result.used_evidence_ids == ["evidence-1"]
    assert result.visible_evidence_ids == ["evidence-1"]
    assert len(result.references) == 1
    reference = result.references[0]
    assert reference.evidence_id == "evidence-1"
    assert reference.retrieval_score == 0.91
    assert reference.graph_version_id == 7
    assert reference.tenant_ref == "jobgraph-platform-public"
    assert reference.permission_scope == "platform:public"
    assert result.permission.assembled_by == "main-system-bff"
    assert result.permission.user_id == "account-1"


def _multi_position_payload(**overrides: Any) -> dict[str, Any]:
    payload = bff_payload(
        business_object={
            "object_type": "standard_position",
            "object_id": "position-a",
            "object_version": "27",
        },
        business_objects=[
            {
                "object_type": "standard_position",
                "object_id": "position-a",
                "object_version": "27",
            },
            {
                "object_type": "standard_position",
                "object_id": "position-b",
                "object_version": "40",
            },
            {
                "object_type": "standard_position",
                "object_id": "position-c",
                "object_version": "14",
            },
        ],
        query_text="这些岗位有什么区别？",
        version_scope="multi_object",
        graph_version_id=None,
        graph_version=None,
        business_version=None,
    )
    payload.update(overrides)
    return payload


def _multi_position_llm(used_evidence_id: str = "evidence-a") -> FakeLlm:
    return FakeLlm(
        answer=EvidenceRagLlmAnswer(
            answer="三个岗位的 GraphVersion 分别为 27、40、14。",
            supported=True,
            used_evidence_ids=(used_evidence_id,),
        ),
        entailments=(
            EvidenceEntailment(
                claim="三个岗位使用各自固定的 GraphVersion",
                relation="support",
                used_evidence_ids=(used_evidence_id,),
                reason="岗位证据明确给出逐对象版本",
                dimension="version_scope",
            ),
        ),
    )


def test_single_object_version_scope_keeps_strict_reference_version():
    result = handlers(
        store=FakeStore(
            hits=(
                hit(
                    business_object_id="position-a",
                    graph_version_id=27,
                ),
            )
        )
    ).query(
        ADMIN,
        bff_payload(
            business_object={
                "object_type": "standard_position",
                "object_id": "position-a",
                "object_version": "27",
            },
            graph_version_id=27,
        ),
    )

    assert result.status == "answered"
    assert result.version_scope == "single_object"
    assert result.references[0].graph_version_id == 27


def test_single_object_version_scope_rejects_reference_from_another_version():
    result = handlers(
        store=FakeStore(
            hits=(
                hit(
                    business_object_id="position-a",
                    graph_version_id=26,
                ),
            )
        )
    ).query(
        ADMIN,
        bff_payload(
            business_object={
                "object_type": "standard_position",
                "object_id": "position-a",
                "object_version": "27",
            },
            graph_version_id=27,
        ),
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID"


def test_multi_object_query_accepts_distinct_graph_versions_in_response():
    store = FakeStore(
        hits=(
            hit(
                "evidence-a",
                business_object_id="position-a",
                graph_version_id=27,
            ),
            hit(
                "evidence-b",
                business_object_id="position-b",
                graph_version_id=40,
            ),
            hit(
                "evidence-c",
                business_object_id="position-c",
                graph_version_id=14,
            ),
        ),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(store=store, llm=_multi_position_llm()).query(
        ADMIN,
        _multi_position_payload(),
    )

    assert result.status == "answered"
    assert result.version_scope == "multi_object"
    assert result.graph_version_id is None
    assert result.graph_version is None
    assert result.business_version is None
    assert {
        (reference.business_object_id, reference.graph_version_id)
        for reference in result.references
    } == {
        ("position-a", 27),
        ("position-b", 40),
        ("position-c", 14),
    }


def _multi_objects(count: int) -> list[dict[str, str]]:
    return [
        {
            "object_type": "standard_position",
            "object_id": f"position-{index}",
            "object_version": str(index + 10),
        }
        for index in range(count)
    ]


def _multi_objects_payload(
    objects: list[dict[str, str]], **overrides: Any
) -> dict[str, Any]:
    payload = _multi_position_payload(
        business_object=objects[0],
        business_objects=objects,
    )
    payload.update(overrides)
    return payload


def _multi_object_hits(
    objects: list[dict[str, str]],
    *,
    quote_by_object: dict[str, str] | None = None,
    score_by_object: dict[str, float] | None = None,
    hits_per_object: int = 3,
) -> tuple[EvidenceRagHit, ...]:
    quote_by_object = quote_by_object or {}
    score_by_object = score_by_object or {}
    hits: list[EvidenceRagHit] = []
    for item in objects:
        object_id = item["object_id"]
        version_id = int(item["object_version"])
        for rank in range(hits_per_object):
            evidence_id = (
                "evidence-a"
                if object_id == "position-0" and rank == 0
                else f"{object_id}-evidence-{rank}"
            )
            hits.append(
                hit(
                    evidence_id,
                    score=score_by_object.get(object_id, 0.90) - rank * 0.01,
                    quote=quote_by_object.get(object_id, "岗位证据"),
                    business_object_id=object_id,
                    graph_version_id=version_id,
                )
            )
    return tuple(hits)


def test_multi_object_compare_retrieves_three_hits_per_object_and_reports_coverage():
    objects = _multi_objects(9)
    store = FakeStore(
        hits=_multi_object_hits(objects),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(
        store=store,
        llm=_multi_position_llm(),
    ).query(ADMIN, _multi_objects_payload(objects, query_text="这些岗位有什么区别？"))

    assert result.status == "answered"
    assert result.coverage is not None
    assert result.coverage.selected_object_count == 9
    assert result.coverage.objects_with_candidates == 9
    assert result.coverage.objects_with_visible_evidence == 9
    assert set(result.coverage.evidence_count_by_object.values()) == {3}
    assert len(result.references) == 27
    assert len(store.search_queries) == 9
    assert [query.top_k for query in store.search_queries] == [3] * 9
    assert [query.graph_version_id for query in store.search_queries] == list(
        range(10, 19)
    )


def test_multi_object_balanced_merge_prevents_high_score_object_from_filling_cap():
    objects = _multi_objects(4)
    store = FakeStore(
        hits=_multi_object_hits(
            objects,
            score_by_object={
                "position-0": 0.99,
                "position-1": 0.70,
                "position-2": 0.69,
                "position-3": 0.68,
            },
        ),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(
        store=store,
        llm=_multi_position_llm(),
        multi_object_max_hits=5,
    ).query(ADMIN, _multi_objects_payload(objects))

    assert result.status == "answered"
    assert [reference.business_object_id for reference in result.references] == [
        "position-0",
        "position-1",
        "position-2",
        "position-3",
        "position-0",
    ]


def test_multi_object_cap_keeps_round_robin_coverage_for_fifteen_objects():
    objects = _multi_objects(15)
    store = FakeStore(
        hits=_multi_object_hits(objects),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(
        store=store,
        llm=_multi_position_llm(),
        multi_object_max_hits=40,
    ).query(ADMIN, _multi_objects_payload(objects))

    assert result.status == "answered"
    assert len(result.references) == 40
    assert result.coverage is not None
    assert result.coverage.objects_with_visible_evidence == 15
    assert set(
        result.coverage.evidence_count_by_object[object_id]
        for object_id in ("position-0", "position-9")
    ) == {3}
    assert set(
        result.coverage.evidence_count_by_object[object_id]
        for object_id in ("position-10", "position-14")
    ) == {2}


def test_multi_object_does_not_force_below_threshold_evidence_for_empty_object():
    objects = _multi_objects(2)
    store = FakeStore(
        hits=_multi_object_hits(
            objects,
            score_by_object={"position-0": 0.90, "position-1": 0.20},
        ),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(
        store=store,
        llm=_multi_position_llm(),
        min_score=0.5,
    ).query(ADMIN, _multi_objects_payload(objects))

    assert result.status == "answered"
    assert result.coverage is not None
    assert result.coverage.objects_with_candidates == 1
    assert result.coverage.objects_with_visible_evidence == 1
    assert result.coverage.evidence_count_by_object == {
        "position-0": 3,
        "position-1": 0,
    }
    assert all(
        reference.business_object_id == "position-0"
        for reference in result.references
    )


def test_multi_object_context_trimming_continues_to_later_objects_after_oversized_block():
    objects = _multi_objects(3)
    store = FakeStore(
        hits=_multi_object_hits(
            objects,
            quote_by_object={"position-0": "长" * 800},
            hits_per_object=1,
        ),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(
        store=store,
        llm=_multi_position_llm("position-1-evidence-0"),
        multi_object_max_context_chars=700,
    ).query(ADMIN, _multi_objects_payload(objects))

    assert result.status == "answered"
    assert result.visible_evidence_ids == [
        "position-1-evidence-0",
        "position-2-evidence-0",
    ]
    assert result.coverage is not None
    assert result.coverage.objects_with_visible_evidence == 2


def test_multi_object_prompt_receives_all_selected_objects_and_visible_coverage():
    objects = _multi_objects(3)
    store = FakeStore(
        hits=_multi_object_hits(objects[:1], hits_per_object=1),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )
    llm = _multi_position_llm()

    result = handlers(store=store, llm=llm).query(
        ADMIN, _multi_objects_payload(objects)
    )

    assert result.status == "answered"
    assert llm.last_business_object_ids == (
        "position-0",
        "position-1",
        "position-2",
    )
    assert llm.last_objects_with_evidence == ("position-0",)
    assert "对象=position-0" in llm.last_evidence_text


def test_deepseek_multi_object_prompt_requires_each_object_or_explicit_insufficiency():
    from types import SimpleNamespace

    from app.infrastructure.evidence_rag_llm import DeepSeekEvidenceRagLlm

    calls: list[tuple[str, str]] = []

    class Client:
        def extract(self, system_prompt: str, user_prompt: str):
            calls.append((system_prompt, user_prompt))
            return SimpleNamespace(
                data={
                    "claims": [
                        {
                            "claim": "岗位 A 有证据支持",
                            "relation": "support",
                            "used_evidence_ids": ["evidence-a"],
                            "reason": "Evidence 直接支持",
                            "dimension": "position",
                        }
                    ],
                    "answer": {
                        "answer": "岗位 A 有证据，岗位 B 现有证据不足。",
                        "supported": True,
                        "used_evidence_ids": ["evidence-a"],
                    },
                }
            )

    llm = DeepSeekEvidenceRagLlm(
        model="deepseek-test",
        algorithm_version="test-v1",
        client_factory=lambda: Client(),
    )
    llm.judge_and_answer(
        query_text="这些岗位有什么区别？",
        evidence_text="[1] 对象=position-a evidence_id=evidence-a",
        query_depth="compare",
        business_object_ids=("position-a", "position-b"),
        objects_with_evidence=("position-a",),
    )

    assert len(calls) == 1
    system_prompt = calls[0][0]
    assert "position-a" in system_prompt
    assert "position-b" in system_prompt
    assert "现有证据不足" in system_prompt
    assert "不得用其他岗位证据代替" in system_prompt


def test_multi_object_context_budget_scales_without_changing_single_object_budget():
    service = handlers(multi_object_max_context_chars=30_000)
    permission = service.permission_for(ADMIN)

    assert service._context_budget(
        service._query(permission, bff_payload())
    ) == 8_000
    assert service._context_budget(
        service._query(permission, _multi_objects_payload(_multi_objects(2)))
    ) == 12_000
    assert service._context_budget(
        service._query(permission, _multi_objects_payload(_multi_objects(6)))
    ) == 18_000
    assert service._context_budget(
        service._query(permission, _multi_objects_payload(_multi_objects(9)))
    ) == 24_000


@pytest.mark.parametrize(
    "invalid_hit",
    [
        hit(
            business_object_id="position-a",
            graph_version_id=26,
        ),
        hit(
            business_object_id="position-x",
            graph_version_id=14,
        ),
    ],
    ids=["wrong-version", "foreign-object"],
)
def test_multi_object_query_rejects_reference_outside_requested_version_scope(
    invalid_hit: EvidenceRagHit,
):
    result = handlers(store=FakeStore(hits=(invalid_hit,))).query(
        ADMIN,
        _multi_position_payload(),
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID"


def test_response_contract_error_returns_stable_failed_response(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    import importlib

    application = importlib.import_module(
        "app.contexts.evidence_rag.application"
    )
    contract_error = ValidationError.from_exception_data(
        "EvidenceEntailmentV1",
        [{"type": "string_type", "loc": ("claim",), "input": None}],
    )

    def raise_contract_error(_item: EvidenceEntailment):
        raise contract_error

    monkeypatch.setattr(application, "_entailment_model", raise_contract_error)
    with caplog.at_level(logging.ERROR, logger=application.logger.name):
        result = handlers(store=FakeStore(hits=(hit(),))).query(
            ADMIN,
            bff_payload(),
        )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_RESPONSE_CONTRACT_INVALID"
    assert "EVIDENCE_RESPONSE_CONTRACT_INVALID" in caplog.text


@pytest.mark.parametrize(
    ("source_type", "source_id", "business_id", "resource_id", "route"),
    [
        (
            "published_jd_fact",
            "jd-1",
            "jd-1",
            "jd-1",
            "/data/jds?jdId=jd-1",
        ),
        (
            "validated_cv_snapshot",
            "snapshot-1",
            "profile-1",
            "resume-1",
            "/profile/resumes?resumeId=resume-1",
        ),
        (
            "position_profile",
            "position-1",
            "position-1",
            "position-1",
            "/positions/position-1",
        ),
        (
            "matching_evidence",
            "evaluation-1",
            "evaluation-1",
            "evaluation-1",
            "/matching/reports/evaluation-1",
        ),
    ],
)
def test_citation_resolver_closes_supported_evidence_targets(
    source_type: str,
    source_id: str,
    business_id: str,
    resource_id: str,
    route: str,
):
    citation_hit = replace(
        hit(),
        source_object_type=source_type,
        source_object_id=source_id,
        business_object_id=business_id,
        highlight_text="负责 RAG 应用开发",
    )

    result = handlers(store=FakeStore(hits=(citation_hit,))).resolve_citation(
        ADMIN,
        freeze_json_object(
            {
                "evidence_id": "evidence-1",
                "source_version": "v1",
                "graph_version_id": 7,
            }
        ),
    )

    assert result["resource_id"] == resource_id
    assert str(result["target_route"]).startswith(route)
    assert "citationEvidenceId=evidence-1" in str(result["target_route"])
    assert result["version_id"] == 7
    assert result["start"] == 0
    assert result["end"] == 9
    assert result["highlight_text"] == "负责 RAG 应用开发"
    assert EvidenceCitationResolution.model_validate(dict(result)).contract_version == (
        "evidence-citation-resolution.v1"
    )


def test_citation_resolver_rejects_wrong_version_with_stable_error():
    with pytest.raises(EvidenceRagError) as exc:
        handlers(store=FakeStore(hits=(hit(),))).resolve_citation(
            ADMIN,
            freeze_json_object(
                {
                    "evidence_id": "evidence-1",
                    "source_version": "stale",
                    "graph_version_id": 7,
                }
            ),
        )

    assert exc.value.code == "CITATION_VERSION_INVALID"


def test_citation_resolver_rejects_unknown_evidence_with_stable_error():
    with pytest.raises(EvidenceRagError) as exc:
        handlers(store=FakeStore()).resolve_citation(
            ADMIN,
            freeze_json_object(
                {"evidence_id": "missing", "source_version": "v1"}
            ),
        )

    assert exc.value.code == "CITATION_NOT_FOUND"


def test_citation_resolver_rejects_cross_tenant_without_returning_target():
    private_hit = replace(
        hit(),
        tenant_ref="enterprise:other",
        permission_scope="enterprise:other",
    )

    with pytest.raises(EvidenceRagError) as exc:
        handlers(store=FakeStore(hits=(private_hit,))).resolve_citation(
            PERSONAL,
            freeze_json_object(
                {
                    "evidence_id": "evidence-1",
                    "source_version": "v1",
                    "graph_version_id": 7,
                }
            ),
        )

    assert exc.value.code == "CITATION_PERMISSION_DENIED"


def test_query_answered_includes_entailment_relations():
    result = handlers(store=FakeStore(hits=(hit(),))).query(
        ADMIN, bff_payload()
    )

    assert result.status == "answered"
    assert len(result.entailment) == 1
    assert result.entailment[0].relation == "support"
    assert result.entailment[0].used_evidence_ids == ["evidence-1"]


def test_query_contradiction_returns_contradicted_insufficient():
    llm = FakeLlm(
        answer=EvidenceRagLlmAnswer(
            answer="根据现有 Evidence，无法支持零编程经验进入该岗位。",
            supported=False,
            used_evidence_ids=(),
        ),
        entailments=(
            EvidenceEntailment(
                claim="候选人可以零编程经验进入",
                relation="contradict",
                used_evidence_ids=("evidence-1",),
                reason="Evidence 明确要求至少掌握一门后端语言",
                dimension="programming_skill",
            ),
        ),
    )

    result = handlers(store=FakeStore(hits=(hit(),)), llm=llm).query(
        ADMIN, bff_payload()
    )

    assert result.status == "insufficient_evidence"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_CONTRADICTED"
    assert "无法支持" in result.error.message
    assert len(result.entailment) == 1
    assert result.entailment[0].relation == "contradict"


def test_query_includes_business_object_label_in_llm_context():
    llm = FakeLlm()
    service = handlers(store=FakeStore(hits=(hit(),)), llm=llm)

    result = service.query(
        ADMIN, bff_payload(business_object_label="后端开发工程师")
    )

    assert result.status == "answered"
    assert "后端开发工程师" in llm.last_evidence_text


def test_query_context_attributes_evidence_to_its_position():
    llm = FakeLlm()
    service = handlers(
        store=FakeStore(hits=(hit(business_object_name="后端开发工程师"),)),
        llm=llm,
    )

    result = service.query(ADMIN, bff_payload())

    assert result.status == "answered"
    assert "岗位=后端开发工程师" in llm.last_evidence_text


def test_query_includes_position_profile_reference():
    llm = FakeLlm()
    service = handlers(
        store=FakeStore(hits=(hit(),)),
        llm=llm,
        profile_text_provider=lambda payload: (
            "岗位画像：岗位=后端开发工程师；学历要求：本科及以上"
        ),
    )

    result = service.query(ADMIN, bff_payload())

    assert result.status == "answered"
    assert "岗位画像 evidence_id=position-profile:7" in llm.last_evidence_text
    profile = next(
        reference
        for reference in result.references
        if reference.evidence_id.startswith("position-profile:")
    )
    assert profile.source_object_type == "position_profile"
    assert "本科及以上" in profile.quote


def test_qualification_question_augments_retrieval_query():
    embedding = FakeEmbedding()
    service = handlers(
        store=FakeStore(hits=(hit(),)),
        embedding=embedding,
    )

    service.query(ADMIN, bff_payload(query_text="我是小学生，能参加吗？"))

    assert "这个岗位要求什么学历？" in embedding.last_text


def test_normal_question_keeps_retrieval_query_unchanged():
    embedding = FakeEmbedding()
    service = handlers(
        store=FakeStore(hits=(hit(),)),
        embedding=embedding,
    )

    service.query(ADMIN, bff_payload(query_text="岗位职责是什么？"))

    assert embedding.last_text == "岗位职责是什么？"


def test_context_limit_never_returns_invisible_reference():
    hits = (
        hit("evidence-1", quote="长" * 120),
        hit("evidence-2", quote="长" * 120),
        hit("evidence-3", quote="长" * 120),
    )
    service = handlers(store=FakeStore(hits=hits), max_context_chars=430)

    result = service.query(ADMIN, bff_payload())

    assert result.status == "answered"
    assert result.visible_evidence_ids == ["evidence-1", "evidence-2"]
    assert [reference.evidence_id for reference in result.references] == [
        "evidence-1",
        "evidence-2",
    ]
    assert "evidence-3" not in result.visible_evidence_ids
    assert "evidence-3" not in result.used_evidence_ids


def test_llm_cannot_use_invisible_evidence():
    llm = FakeLlm(
        answer=EvidenceRagLlmAnswer(
            answer="候选人具备 RAG 项目经验。",
            supported=True,
            used_evidence_ids=("evidence-9",),
        )
    )

    result = handlers(store=FakeStore(hits=(hit(),)), llm=llm).query(
        ADMIN, bff_payload()
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_RAG_USED_EVIDENCE_INVALID"


def test_supported_answer_without_used_evidence_is_not_grounded():
    llm = FakeLlm(
        answer=EvidenceRagLlmAnswer(
            answer="候选人具备 RAG 项目经验。",
            supported=True,
            used_evidence_ids=(),
        )
    )

    result = handlers(store=FakeStore(hits=(hit(),)), llm=llm).query(
        ADMIN, bff_payload()
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_RAG_GROUNDING_INVALID"


def test_unsupported_answer_is_insufficient_evidence():
    llm = FakeLlm(
        answer=EvidenceRagLlmAnswer(
            answer="证据不足，无法回答。",
            supported=False,
            used_evidence_ids=(),
        )
    )

    result = handlers(store=FakeStore(hits=(hit(),)), llm=llm).query(
        ADMIN, bff_payload()
    )

    assert result.status == "insufficient_evidence"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_INSUFFICIENT"
    assert result.references == []


def test_weak_hits_below_threshold_are_insufficient():
    result = handlers(
        store=FakeStore(hits=(hit("evidence-weak", score=0.1),)),
        min_score=0.5,
    ).query(ADMIN, bff_payload())

    assert result.status == "insufficient_evidence"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_BELOW_THRESHOLD"
    assert result.references == []


def test_query_accepts_frozen_bff_payload_with_array_evidence_types():
    frozen = freeze_json_object(bff_payload())

    result = handlers(store=FakeStore(hits=(hit(),))).query(ADMIN, frozen)

    assert result.status == "answered"
    assert len(result.references) == 1


def test_query_returns_insufficient_evidence_when_store_has_no_hits():
    result = handlers(store=FakeStore(hits=())).query(ADMIN, bff_payload())

    assert result.status == "insufficient_evidence"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_NOT_FOUND"
    assert result.answer is None
    assert result.references == []


def test_query_reports_index_not_ready_while_index_is_building():
    service = handlers(
        store=FakeStore(hits=()),
        index_status_provider=lambda payload: {
            "status": "running",
            "indexed_count": 10,
            "expected_count": 100,
        },
    )

    result = service.query(ADMIN, bff_payload())

    assert result.status == "insufficient_evidence"
    assert result.error is not None
    assert result.error.code == "EVIDENCE_INDEX_NOT_READY"
    assert result.references == []


def test_query_keeps_evidence_not_found_after_index_completed():
    service = handlers(
        store=FakeStore(hits=()),
        index_status_provider=lambda payload: {
            "status": "completed",
            "indexed_count": 100,
            "expected_count": 100,
        },
    )

    result = service.query(ADMIN, bff_payload())

    assert result.error is not None
    assert result.error.code == "EVIDENCE_NOT_FOUND"


def test_query_returns_failed_when_embedding_is_unavailable():
    embedding = FakeEmbedding(
        error=EvidenceRagError("EMBEDDING_UNAVAILABLE", "down")
    )

    result = handlers(embedding=embedding).query(ADMIN, bff_payload())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "EMBEDDING_UNAVAILABLE"


def test_query_returns_failed_when_llm_is_unavailable():
    llm = FakeLlm(error=EvidenceRagError("LLM_PROVIDER_UNAVAILABLE", "down"))

    result = handlers(llm=llm, store=FakeStore(hits=(hit(),))).query(
        ADMIN, bff_payload()
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "LLM_PROVIDER_UNAVAILABLE"


def test_query_returns_failed_when_disabled():
    result = handlers(enabled=False).query(ADMIN, bff_payload())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "RAG_EVIDENCE_DISABLED"


def test_query_returns_failed_when_permission_resolution_denied():
    from app.domain.errors import PermissionDenied

    def denied(_actor):
        raise PermissionDenied("no enterprise")

    result = handlers(resolver=denied).query(ADMIN, bff_payload())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_index_requires_internal_role_and_indexes_records():
    store = FakeStore()
    service = handlers(store=store)
    item = {
        "evidence_id": "evidence-1",
        "business_object_type": "cv_profile",
        "business_object_id": "profile-1",
        "evidence_type": "cv_evidence",
        "source_object_type": "validated_cv_snapshot",
        "source_object_id": "snapshot-1",
        "source_document_id": "document-1",
        "source_version": "v1",
        "text": "负责 RAG 应用开发",
        "tenant_ref": "jobgraph-platform-public",
        "permission_scope": "platform:public",
        "quote": "负责 RAG 应用开发",
        "location_start": 0,
        "location_end": 9,
        "alignment": "exact",
        "graph_version_id": 7,
    }

    result = service.index(ADMIN, (item,))
    with pytest.raises(PermissionDenied):
        service.index(PERSONAL, (item,))

    assert result["indexed_count"] == 1
    assert store.upserted[0][1] == [0.1, 0.2]


def test_index_uses_batch_embedding_and_upsert():
    store = FakeStore()
    service = handlers(store=store)
    items = []
    for index in range(3):
        item = {
            "evidence_id": f"evidence-{index}",
            "business_object_type": "cv_profile",
            "business_object_id": "profile-1",
            "evidence_type": "cv_evidence",
            "source_object_type": "validated_cv_snapshot",
            "source_object_id": "snapshot-1",
            "source_document_id": "document-1",
            "source_version": "v1",
            "text": f"负责 RAG 应用开发 {index}",
            "tenant_ref": "jobgraph-platform-public",
            "permission_scope": "platform:public",
            "quote": f"负责 RAG 应用开发 {index}",
            "location_start": 0,
            "location_end": 9,
            "alignment": "exact",
            "graph_version_id": 7,
        }
        items.append(item)

    result = service.index(ADMIN, tuple(items))

    assert result["indexed_count"] == 3
    assert len(store.upsert_many_calls) == 1
    assert len(store.upsert_many_calls[0][0]) == 3
    assert len(store.upsert_many_calls[0][1]) == 3


def test_index_accepts_graph_version_id_and_name_together():
    store = FakeStore()
    service = handlers(store=store)
    item = {
        "evidence_id": "evidence-1",
        "business_object_type": "cv_profile",
        "business_object_id": "profile-1",
        "evidence_type": "cv_evidence",
        "source_object_type": "validated_cv_snapshot",
        "source_object_id": "snapshot-1",
        "source_document_id": "document-1",
        "source_version": "v1",
        "text": "负责 RAG 应用开发",
        "tenant_ref": "jobgraph-platform-public",
        "permission_scope": "platform:public",
        "quote": "负责 RAG 应用开发",
        "location_start": 0,
        "location_end": 9,
        "alignment": "exact",
        "graph_version_id": 94,
        "graph_version": "2026-q2",
    }

    result = service.index(ADMIN, (item,))

    assert result["indexed_count"] == 1
    record = store.upserted[0][0]
    assert record.graph_version_id == 94
    assert record.graph_version == "2026-q2"


def test_index_record_rejects_business_version_with_graph_version():
    from app.contexts.evidence_rag.contracts import EvidenceRagRecord

    with pytest.raises(ValueError):
        EvidenceRagRecord(
            evidence_id="evidence-1",
            business_object_type="cv_profile",
            business_object_id="profile-1",
            evidence_type="cv_evidence",
            source_object_type="validated_cv_snapshot",
            source_object_id="snapshot-1",
            source_document_id="document-1",
            source_version="v1",
            text="负责 RAG 应用开发",
            tenant_ref="jobgraph-platform-public",
            permission_scope="platform:public",
            quote="负责 RAG 应用开发",
            location_start=0,
            location_end=9,
            alignment="exact",
            graph_version="2026-q2",
            business_version="snapshot-1",
        )


def test_bff_schema_accepts_client_permission_but_server_overrides_it():
    payload = bff_payload(
        permission={
            "user_id": "spoofed",
            "tenant_ref": "spoofed-tenant",
            "permission_scope": "spoofed-scope",
            "assembled_by": "main-system-bff",
        }
    )

    request = EvidenceRagBFFRequest.model_validate(payload)
    result = handlers(store=FakeStore(hits=(hit(),))).query(
        ADMIN, request.model_dump(mode="json")
    )

    assert result.permission.tenant_ref == "jobgraph-platform-public"
    assert result.permission.permission_scope == "platform:public"


def test_bff_schema_requires_exactly_one_version_identity():
    payload = bff_payload(graph_version_id=None, graph_version=None)

    with pytest.raises(ValidationError):
        EvidenceRagBFFRequest.model_validate(payload)


def test_bff_schema_accepts_multiple_business_objects():
    payload = bff_payload(
        business_object={
            "object_type": "standard_position",
            "object_id": "position-1",
            "object_version": "7",
        },
        business_objects=[
            {
                "object_type": "standard_position",
                "object_id": "position-1",
                "object_version": "7",
            },
            {
                "object_type": "standard_position",
                "object_id": "position-2",
                "object_version": "11",
            },
        ],
        version_scope="multi_object",
        graph_version_id=None,
    )

    request = EvidenceRagBFFRequest.model_validate(payload)

    assert request.version_scope == "multi_object"
    assert request.business_objects is not None
    assert request.business_objects[0].object_id == "position-1"
    assert request.business_objects[1].object_version == "11"


def test_bff_schema_rejects_global_version_for_multiple_business_objects():
    with pytest.raises(ValidationError, match="global version identity"):
        EvidenceRagBFFRequest.model_validate(
            _multi_position_payload(graph_version_id=27)
        )


def test_query_keeps_each_business_object_graph_version_and_conversation_context():
    payload = bff_payload(
        business_object={
            "object_type": "standard_position",
            "object_id": "position-1",
            "object_version": "7",
        },
        business_objects=[
            {
                "object_type": "standard_position",
                "object_id": "position-1",
                "object_version": "7",
            },
            {
                "object_type": "standard_position",
                "object_id": "position-2",
                "object_version": "11",
            },
        ],
        version_scope="multi_object",
        graph_version_id=None,
        conversation_history=[
            {"role": "user", "text": "先比较两个岗位"},
            {"role": "assistant", "text": "它们都要求工程能力。"},
        ],
        query_text="哪个更重视 RAG？",
    )
    request = EvidenceRagBFFRequest.model_validate(payload)
    store = FakeStore(
        hits=(hit(business_object_id="position-1", graph_version_id=7),),
        filter_multi_object_calls=True,
        respect_query_top_k=True,
    )

    result = handlers(store=store).query(
        ADMIN, request.model_dump(mode="json")
    )

    assert result.status == "answered"
    assert [
        (query.business_object_id, query.graph_version_id, query.top_k)
        for query in store.search_queries
    ] == [("position-1", 7, 3), ("position-2", 11, 3)]
    assert all(query.version_scope == "single_object" for query in store.search_queries)
    assert all(query.retrieval_text == "哪个更重视 RAG？" for query in store.search_queries)
    assert all("先比较两个岗位" in query.query_text for query in store.search_queries)
    assert all(
        "用户当前问题：哪个更重视 RAG？" in query.query_text
        for query in store.search_queries
    )


@pytest.mark.parametrize(
    ("question", "expected_depth", "expected_top_k"),
    [
        ("这个岗位要求 Python 吗？", "fact", 5),
        ("Java 后端和大模型算法岗有什么区别？", "compare", 8),
        ("比较 Java 后端和大模型算法岗的主要区别", "compare", 8),
        ("大模型算法工程师主要需要哪些能力？", "overview", 10),
        ("Python 是常见要求吗？", "overview", 10),
    ],
)
def test_query_depth_classification_and_dynamic_top_k(
    question: str,
    expected_depth: str,
    expected_top_k: int,
):
    store = FakeStore()

    handlers(store=store).query(ADMIN, bff_payload(query_text=question))

    assert store.last_query is not None
    assert store.last_query.query_depth == expected_depth
    assert store.last_query.top_k == expected_top_k


def test_score_decay_truncates_weak_evidence_after_top_hits():
    hits = (
        hit("evidence-1", score=0.90),
        hit("evidence-2", score=0.88),
        hit("evidence-3", score=0.87),
        hit("evidence-4", score=0.50),
        hit("evidence-5", score=0.49),
    )

    result = handlers(store=FakeStore(hits=hits)).query(
        ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？")
    )

    assert result.status == "answered"
    assert result.visible_evidence_ids == [
        "evidence-1",
        "evidence-2",
        "evidence-3",
    ]
    assert [reference.evidence_id for reference in result.references] == [
        "evidence-1",
        "evidence-2",
        "evidence-3",
    ]


def test_score_decay_keeps_sustained_high_scores():
    hits = (
        hit("evidence-1", score=0.91),
        hit("evidence-2", score=0.90),
        hit("evidence-3", score=0.89),
        hit("evidence-4", score=0.88),
        hit("evidence-5", score=0.87),
    )

    result = handlers(store=FakeStore(hits=hits)).query(
        ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？")
    )

    assert result.status == "answered"
    assert result.visible_evidence_ids == [
        "evidence-1",
        "evidence-2",
        "evidence-3",
        "evidence-4",
        "evidence-5",
    ]


def test_score_decay_keeps_minimum_three_hits():
    hits = (
        hit("evidence-1", score=0.80),
        hit("evidence-2", score=0.50),
        hit("evidence-3", score=0.50),
        hit("evidence-4", score=0.10),
        hit("evidence-5", score=0.10),
    )

    result = handlers(store=FakeStore(hits=hits)).query(
        ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？")
    )

    assert result.status == "answered"
    assert result.visible_evidence_ids == [
        "evidence-1",
        "evidence-2",
        "evidence-3",
    ]


def test_score_decay_respects_min_score():
    hits = (
        hit("evidence-1", score=0.90),
        hit("evidence-2", score=0.80),
        hit("evidence-3", score=0.40),
        hit("evidence-4", score=0.30),
    )

    result = handlers(
        store=FakeStore(hits=hits),
        min_score=0.5,
    ).query(ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？"))

    assert result.status == "answered"
    assert result.visible_evidence_ids == ["evidence-1", "evidence-2"]


def test_min_score_is_hard_gate_when_fewer_than_three_survive():
    hits = (
        hit("evidence-1", score=0.80),
        hit("evidence-2", score=0.70),
        hit("evidence-3", score=0.40),
        hit("evidence-4", score=0.30),
        hit("evidence-5", score=0.20),
    )

    result = handlers(
        store=FakeStore(hits=hits),
        min_score=0.5,
    ).query(ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？"))

    assert result.status == "answered"
    assert result.visible_evidence_ids == ["evidence-1", "evidence-2"]


def test_min_score_zero_preserves_legacy_filtering_behavior():
    hits = (
        hit("evidence-1", score=0.80),
        hit("evidence-2", score=0.20),
        hit("evidence-3", score=0.10),
    )

    result = handlers(store=FakeStore(hits=hits)).query(
        ADMIN, bff_payload(query_text="这个岗位要求 Python 吗？")
    )

    assert result.status == "answered"
    assert result.visible_evidence_ids == ["evidence-1", "evidence-2", "evidence-3"]


def test_rag_endpoints_are_declared_and_authenticated():
    from app.main import app

    paths = app.openapi()["paths"]
    expected = [
        ("post", "/api/v1/rag/evidence"),
        ("post", "/api/v1/rag/evidence/citations/resolve"),
        ("post", "/api/v1/rag/evidence/index"),
        ("post", "/api/v1/rag/evidence/invalidate"),
        ("delete", "/api/v1/rag/evidence"),
    ]
    for method, path in expected:
        assert path in paths
        assert method in paths[path]
        assert paths[path][method].get("security")


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeQdrantClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.existing_point_id = "11111111-1111-4111-8111-111111111111"

    def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/collections/"):
            return _FakeResponse(404, {})
        if method == "PUT" and path.startswith("/collections/"):
            return _FakeResponse(200, {"result": True})
        if method == "PUT" and path.endswith("/index"):
            return _FakeResponse(200, {"result": True})
        if method == "PUT" and path.endswith("/points"):
            return _FakeResponse(200, {"result": True})
        if method == "POST" and path.endswith("/points/scroll"):
            return _FakeResponse(
                200,
                {
                    "result": {
                        "points": [
                            {
                                "id": self.existing_point_id,
                                "payload": {
                                    "evidence_id": "evidence-1",
                                    "source_version": "v1",
                                    "graph_version_id": "7",
                                    "graph_version": None,
                                    "business_version": None,
                                },
                            }
                        ],
                    }
                },
            )
        if method == "POST" and path.endswith("/points/search"):
            return _FakeResponse(200, {"result": []})
        if method == "POST" and path.endswith("/points/count"):
            return _FakeResponse(200, {"result": {"count": 3}})
        if method == "POST" and path.endswith("/points/payload"):
            return _FakeResponse(200, {"result": True})
        if method == "POST" and path.endswith("/points/delete"):
            return _FakeResponse(200, {"result": True})
        raise AssertionError(f"unexpected request {method} {path}")


def test_qdrant_store_upsert_reuses_existing_point_id():
    from app.contexts.evidence_rag.contracts import EvidenceRagRecord

    client = _FakeQdrantClient()
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )
    record = EvidenceRagRecord(
        evidence_id="evidence-1",
        business_object_type="cv_profile",
        business_object_id="profile-1",
        evidence_type="cv_evidence",
        source_object_type="validated_cv_snapshot",
        source_object_id="snapshot-1",
        source_document_id="document-1",
        source_version="v1",
        text="负责 RAG 应用开发",
        tenant_ref="jobgraph-platform-public",
        permission_scope="platform:public",
        quote="负责 RAG 应用开发",
        location_start=0,
        location_end=9,
        alignment="exact",
        graph_version_id=7,
    )

    store.upsert(record, [0.1, 0.2])
    store.upsert(record, [0.1, 0.2])

    upserts = [
        call for call in client.calls if call[1].endswith("/points") and call[0] == "PUT"
    ]
    assert len(upserts) == 2
    for _method, _path, kwargs in upserts:
        point = kwargs["json"]["points"][0]
        assert point["id"] == client.existing_point_id
        assert point["vector"] == [0.1, 0.2]


def test_qdrant_store_count_filters_by_business_object_and_version():
    client = _FakeQdrantClient()
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )

    result = store.count(
        business_object_type="cv_profile",
        business_object_id="profile-1",
        graph_version_id=7,
    )

    assert result == 3
    count_call = next(
        call for call in client.calls if call[1].endswith("/points/count")
    )
    must = count_call[2]["json"]["filter"]["must"]
    assert {"key": "business_object_type", "match": {"value": "cv_profile"}} in must
    assert {"key": "business_object_id", "match": {"value": "profile-1"}} in must
    assert {"key": "graph_version_id", "match": {"value": "7"}} in must


class _RecordingSearchClient:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.search_json: dict[str, Any] | None = None

    def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        if method == "GET" and path.startswith("/collections/"):
            return _FakeResponse(404, {})
        if method == "PUT" and path.startswith("/collections/"):
            return _FakeResponse(200, {"result": True})
        if method == "POST" and path.endswith("/points/search"):
            self.search_json = kwargs["json"]
            return _FakeResponse(200, {"result": self.results})
        raise AssertionError(f"unexpected request {method} {path}")


class _RecordingCitationClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.scroll_json: dict[str, Any] | None = None

    def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        if method == "GET" and path.startswith("/collections/"):
            return _FakeResponse(404, {})
        if method == "PUT" and path.startswith("/collections/"):
            return _FakeResponse(200, {"result": True})
        if method == "POST" and path.endswith("/points/scroll"):
            self.scroll_json = kwargs["json"]
            return _FakeResponse(
                200,
                {"result": {"points": [{"id": "citation-1", "payload": self.payload}]}},
            )
        raise AssertionError(f"unexpected request {method} {path}")


def _rag_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_ref": "jobgraph-platform-public",
        "permission_scope": "platform:public",
        "active": True,
        "business_object_type": "cv_profile",
        "business_object_id": "profile-1",
        "evidence_type": "cv_evidence",
        "evidence_id": "evidence-public",
        "source_object_type": "validated_cv_snapshot",
        "source_object_id": "snapshot-1",
        "source_document_id": "document-1",
        "source_version": "v1",
        "quote": "负责 RAG 应用开发",
        "location_start": 0,
        "location_end": 9,
        "occurrence_index": 0,
        "alignment": "exact",
        "graph_version_id": None,
        "graph_version": "2026-q2",
        "business_version": None,
        "text": "负责 RAG 应用开发",
    }
    payload.update(overrides)
    return payload


def _rag_query(
    *,
    tenant_ref: str = "personal:account-2",
    permission_scope: str = "personal:account-2",
) -> Any:
    from app.contexts.evidence_rag.contracts import EvidenceRagQuery

    return EvidenceRagQuery(
        query_text="是否有 RAG 经验",
        business_object_type="cv_profile",
        business_object_id="profile-1",
        evidence_types=("cv_evidence",),
        tenant_ref=tenant_ref,
        permission_scope=permission_scope,
        graph_version="2026-q2",
    )


def test_qdrant_citation_uses_exact_identity_and_returns_highlight_span():
    from app.contexts.evidence_rag import EvidenceCitationQuery

    client = _RecordingCitationClient(
        _rag_payload(evidence_id="evidence-42", graph_version="2026-q2")
    )
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )

    hits = store.citations(
        EvidenceCitationQuery(
            evidence_id="evidence-42",
            source_version="v1",
            graph_version="2026-q2",
        )
    )

    assert len(hits) == 1
    assert hits[0].highlight_text == "负责 RAG 应用开发"
    assert (hits[0].location_start, hits[0].location_end) == (0, 9)
    assert client.scroll_json is not None
    must = client.scroll_json["filter"]["must"]
    assert {"key": "evidence_id", "match": {"value": "evidence-42"}} in must
    assert {"key": "source_version", "match": {"value": "v1"}} in must
    assert {"key": "graph_version", "match": {"value": "2026-q2"}} in must


def test_qdrant_search_makes_platform_public_visible_to_personal_query():
    client = _RecordingSearchClient(
        [{"id": "public-1", "score": 0.9, "payload": _rag_payload()}]
    )
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )

    hits = store.search(_rag_query(), [0.1, 0.2])

    assert len(hits) == 1
    assert hits[0].permission_scope == "platform:public"
    assert hits[0].tenant_ref == "jobgraph-platform-public"
    assert client.search_json is not None
    must = client.search_json["filter"]["must"]
    visibility = next(condition for condition in must if "should" in condition)
    assert any(
        {
            "key": "tenant_ref",
            "match": {"value": "jobgraph-platform-public"},
        }
        in group["must"]
        and {
            "key": "permission_scope",
            "match": {"value": "platform:public"},
        }
        in group["must"]
        for group in visibility["should"]
    )


def test_qdrant_search_pairs_multiple_business_objects_with_versions():
    from app.contexts.evidence_rag.contracts import EvidenceRagQuery

    client = _RecordingSearchClient([])
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )
    query = EvidenceRagQuery(
        query_text="是否有 RAG 经验",
        business_object_type="cv_profile",
        business_object_id="profile-1",
        business_object_ids=("profile-1", "profile-2"),
        business_object_versions=(("profile-1", 7), ("profile-2", 11)),
        evidence_types=("cv_evidence",),
        tenant_ref="jobgraph-platform-public",
        permission_scope="platform:public",
        version_scope="multi_object",
    )

    store.search(query, [0.1, 0.2])

    must = client.search_json["filter"]["must"]
    object_condition = next(
        condition
        for condition in must
        if "should" in condition
        and any(
            {
                "key": "business_object_id",
                "match": {"value": "profile-1"},
            }
            in group.get("must", [])
            for group in condition["should"]
        )
    )
    assert len(object_condition["should"]) == 2
    assert {
        "key": "business_object_id",
        "match": {"value": "profile-1"},
    } in object_condition["should"][0]["must"]
    assert {
        "key": "graph_version_id",
        "match": {"value": "11"},
    } in object_condition["should"][1]["must"]


def test_qdrant_search_pairs_each_business_object_with_its_graph_version():
    from app.contexts.evidence_rag.contracts import EvidenceRagQuery

    client = _RecordingSearchClient([])
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )
    query = EvidenceRagQuery(
        query_text="比较岗位",
        business_object_type="standard_position",
        business_object_id="position-1",
        business_object_ids=("position-1", "position-2"),
        business_object_versions=(("position-1", 7), ("position-2", 11)),
        evidence_types=("jd_evidence",),
        tenant_ref="jobgraph-platform-public",
        permission_scope="platform:public",
        version_scope="multi_object",
    )

    store.search(query, [0.1, 0.2])

    must = client.search_json["filter"]["must"]
    paired = next(
        condition
        for condition in must
        if "should" in condition
        and any(
            {"key": "business_object_id", "match": {"value": "position-1"}}
            in group.get("must", [])
            for group in condition["should"]
        )
    )
    assert len(paired["should"]) == 2
    assert {
        "key": "graph_version_id",
        "match": {"value": "11"},
    } in paired["should"][1]["must"]


def test_qdrant_search_rejects_private_cross_tenant_hit():
    payload = _rag_payload(
        tenant_ref="enterprise:other",
        permission_scope="enterprise:other",
    )
    client = _RecordingSearchClient(
        [{"id": "private-other", "score": 0.9, "payload": payload}]
    )
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )

    with pytest.raises(EvidenceRagError) as exc:
        store.search(_rag_query(), [0.1, 0.2])

    assert exc.value.code == "QDRANT_TENANT_VIOLATION"


def test_qdrant_search_keeps_private_scope_isolation():
    payload = _rag_payload(
        tenant_ref="personal:account-2",
        permission_scope="personal:account-2",
        evidence_id="evidence-private",
    )
    client = _RecordingSearchClient(
        [{"id": "private-own", "score": 0.9, "payload": payload}]
    )
    store = QdrantEvidenceRagStore(
        "http://qdrant:6333",
        collection_name="evidence_rag_test",
        dimension=2,
        client=client,
    )

    hits = store.search(_rag_query(), [0.1, 0.2])

    assert len(hits) == 1
    assert hits[0].evidence_id == "evidence-private"
    assert hits[0].permission_scope == "personal:account-2"


def _public_reference_payload() -> dict[str, Any]:
    return {
        "evidence_id": "evidence-public",
        "source_object_type": "validated_cv_snapshot",
        "source_object_id": "snapshot-1",
        "source_document_id": "document-1",
        "quote": "负责 RAG 应用开发",
        "location_start": 0,
        "location_end": 9,
        "occurrence_index": 0,
        "alignment": "exact",
        "graph_version_id": 7,
        "graph_version": None,
        "business_version": None,
        "source_version": "v1",
        "tenant_ref": "jobgraph-platform-public",
        "permission_scope": "platform:public",
    }


def _answered_response(reference: dict[str, Any]) -> Any:
    from jobgraph_contracts.rag import EvidenceRAGResponseV1

    return EvidenceRAGResponseV1(
        contract_version="evidence-rag-response.v1",
        status="answered",
        answer="候选人具备 RAG 项目经验。",
        references=[reference],
        provider="deepseek",
        model="deepseek-v4-flash",
        model_version="deepseek-evidence-rag-answer.v1",
        trace_id="trace-public-visible",
        permission={
            "user_id": "account-2",
            "tenant_ref": "personal:account-2",
            "permission_scope": "personal:account-2",
            "assembled_by": "main-system-bff",
        },
        graph_version_id=7,
        explanation_only=True,
    )


def test_response_contract_allows_platform_public_reference_for_personal_query():
    response = _answered_response(_public_reference_payload())

    assert response.status == "answered"
    assert response.references[0].permission_scope == "platform:public"


def test_response_contract_rejects_private_cross_tenant_reference():
    reference = _public_reference_payload()
    reference.update(
        tenant_ref="enterprise:other",
        permission_scope="enterprise:other",
    )

    with pytest.raises(ValidationError, match="cannot cross tenant boundaries"):
        _answered_response(reference)
