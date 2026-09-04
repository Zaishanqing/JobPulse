from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contexts.catalog import ManageSkills
from app.contexts.catalog._applications.normalization_suggestions import (
    rank_normalization_suggestions,
)
from app.contexts.catalog._ports.normalization_suggestions import CatalogEmbeddingError
from app.contexts.catalog._ports.skills import (
    SkillAliasRecord,
    SkillRecord,
)
from app.domain.accounts import AccountActor
from app.main import app
from app.infrastructure.catalog_embedding import HttpCatalogEmbedding
from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user


def skill(skill_id: str, name: str, category: str = "开发技术") -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        skill_name=name,
        catalog_code=None,
        category=category,
        description=None,
        parent_skill_id=None,
        status="active",
        redirect_target_skill_id=None,
        created_at=None,
        updated_at=None,
    )


class SemanticEmbedding:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (1.0, 0.0) if index in {0, 2} else (0.0, 1.0)
            for index, _ in enumerate(texts)
        )


class FailedEmbedding:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise CatalogEmbeddingError("unavailable")


class HighSemanticEmbedding:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)


class DualRecallEmbedding:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (1.0, 0.0)
            if index == 0 or str(texts[index]).startswith("LLMOps")
            else (0.0, 1.0)
            for index, _ in enumerate(texts)
        )


def test_exact_alias_reviewed_priority_and_stable_hard_negatives():
    skills = [
        skill("java", "Java"),
        skill("javascript", "JavaScript"),
        skill("react", "React"),
        skill("react-native", "React Native"),
    ]
    aliases = [SkillAliasRecord("a1", "javascript", "JS")]

    exact = rank_normalization_suggestions(
        raw_skill="Java",
        context=None,
        skills=skills,
        aliases=aliases,
        reviewed_skill_id=None,
        top_k=4,
        embedding=None,
    )
    assert exact[0].skill_id == "java"
    assert "名称包含关系" not in exact[1].reasons
    assert exact[0].reasons == ("标准名称精确命中",)

    alias = rank_normalization_suggestions(
        raw_skill="js",
        context=None,
        skills=skills,
        aliases=aliases,
        reviewed_skill_id=None,
        top_k=4,
        embedding=None,
    )
    assert alias[0].skill_id == "javascript"
    assert alias[0].matched_alias == "JS"
    assert alias[0].reasons == ("技能别名精确命中",)

    reviewed = rank_normalization_suggestions(
        raw_skill="React",
        context=None,
        skills=skills,
        aliases=aliases,
        reviewed_skill_id="react-native",
        top_k=4,
        embedding=None,
    )
    assert reviewed[0].skill_id == "react-native"
    assert reviewed[0].reasons == ("历史人工审核已确认",)

    repeated = rank_normalization_suggestions(
        raw_skill="jav",
        context=None,
        skills=list(reversed(skills)),
        aliases=aliases,
        reviewed_skill_id=None,
        top_k=4,
        embedding=None,
    )
    assert [item.skill_id for item in repeated] == [item.skill_id for item in rank_normalization_suggestions(
        raw_skill="jav", context=None, skills=skills, aliases=aliases,
        reviewed_skill_id=None, top_k=4, embedding=None,
    )]


def test_semantic_cosine_participates_in_hybrid_but_never_overrides_exact_priority():
    skills = [skill("python", "Python"), skill("java", "Java")]
    results = rank_normalization_suggestions(
        raw_skill="Pythn",
        context="backend development",
        skills=skills,
        aliases=[],
        reviewed_skill_id=None,
        top_k=2,
        embedding=SemanticEmbedding(),
    )
    java = next(item for item in results if item.skill_id == "java")
    assert java.semantic_available is True
    assert java.semantic_score == 1.0
    assert java.combined_score == pytest.approx(
        0.55 * java.lexical_score + 0.45,
        abs=1e-6,
    )

    exact = rank_normalization_suggestions(
        raw_skill="Python",
        context=None,
        skills=skills,
        aliases=[],
        reviewed_skill_id=None,
        top_k=2,
        embedding=SemanticEmbedding(),
    )
    assert exact[0].skill_id == "python"


def test_dual_recall_recovers_semantic_target_outside_lexical_top_n():
    skills = [
        skill(f"skill-{index:02d}", f"Skill {index:02d}")
        for index in range(70)
    ] + [skill("llmops", "LLMOps")]
    results = rank_normalization_suggestions(
        raw_skill="LLMOps",
        context="大模型平台运维",
        skills=skills,
        aliases=[],
        reviewed_skill_id=None,
        top_k=5,
        embedding=DualRecallEmbedding(),
        lexical_pool_size=40,
        semantic_pool_size=63,
    )
    assert results[0].skill_id == "llmops"
    assert results[0].semantic_available is True
    assert results[0].semantic_score == 1.0


def test_embedding_failure_falls_back_to_lexical_without_partial_semantic_scores():
    results = rank_normalization_suggestions(
        raw_skill="Pythn",
        context=None,
        skills=[skill("python", "Python"), skill("java", "Java")],
        aliases=[],
        reviewed_skill_id=None,
        top_k=2,
        embedding=FailedEmbedding(),
    )
    assert results[0].skill_id == "python"
    assert all(item.semantic_available is False for item in results)
    assert all(item.semantic_score is None for item in results)
    assert all(item.combined_score == item.lexical_score for item in results)


def test_catalog_embedding_adapter_validates_real_v1_contract(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "vectors": [[1.0, 0.0], [0.0, 1.0]],
                "model_id": "catalog-model",
                "model_revision": "fixed-revision",
                "dimension": 2,
                "normalized": True,
                "usage": {"input_count": 2, "character_count": 10},
                "latency_ms": 1.2,
            }

    captured = {}

    def post(url, *, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.infrastructure.catalog_embedding.httpx.post", post)
    adapter = HttpCatalogEmbedding(
        "http://embedding-service:8000",
        model="catalog-model",
        revision="fixed-revision",
        dimension=2,
        timeout_seconds=3,
    )

    assert adapter.embed(("Python", "Java")) == ((1.0, 0.0), (0.0, 1.0))
    assert captured == {
        "url": "http://embedding-service:8000/v1/embeddings",
        "json": {"inputs": ["Python", "Java"], "normalize": True},
        "timeout": 3,
    }


def test_catalog_embedding_adapter_rejects_lineage_mismatch(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "vectors": [[1.0, 0.0]],
                "model_id": "wrong-model",
                "model_revision": "fixed-revision",
                "dimension": 2,
                "normalized": True,
                "usage": {"input_count": 1, "character_count": 6},
                "latency_ms": 1.2,
            }

    monkeypatch.setattr(
        "app.infrastructure.catalog_embedding.httpx.post",
        lambda *_args, **_kwargs: Response(),
    )
    adapter = HttpCatalogEmbedding(
        "http://embedding-service:8000",
        model="catalog-model",
        revision="fixed-revision",
        dimension=2,
    )

    with pytest.raises(CatalogEmbeddingError, match="lineage"):
        adapter.embed(("Python",))


class Repository:
    def __init__(self) -> None:
        self.skills = [skill("python", "Python")]
        self.aliases = [SkillAliasRecord("a1", "python", "Py")]
        self.add_candidate_calls = 0

    def get_candidate_by_expression(self, _value: str):
        return None

    def list_skills(self):
        return self.skills

    def list_aliases(self, _skill_id=None):
        return self.aliases

    def add_candidate(self, *_args, **_kwargs):
        self.add_candidate_calls += 1
        raise AssertionError("suggestion use case must not create candidates")


class UnitOfWork:
    def __init__(self, repository: Repository) -> None:
        self.skills = repository
        self.commit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def commit(self):
        self.commit_calls += 1


def test_suggestion_use_case_has_no_write_side_effect_even_with_semantic_top_one():
    repository = Repository()
    uow = UnitOfWork(repository)
    use_case = ManageSkills(lambda: uow, normalization_embedding=HighSemanticEmbedding())
    actor = AccountActor("reviewer-1", "reviewer")

    result = use_case.suggest_normalizations(actor, "Py", "Python backend", 1)

    assert result[0].skill_id == "python"
    assert result[0].semantic_score == 1.0
    assert repository.add_candidate_calls == 0
    assert uow.commit_calls == 0


client = TestClient(app)


@pytest.fixture
def clean_database():
    reset_database_data()
    yield
    reset_database_data()


def _token() -> str:
    create_internal_user("suggestion_reviewer", "reviewer")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "reviewer",
            "username": "suggestion_reviewer",
            "password": "password123",
            "email": "suggestion@example.com",
            "phone": "13800000000",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "suggestion_reviewer", "password": "password123"},
    )
    return response.json()["data"]["access_token"]


@pytest.mark.usefixtures("clean_database")
def test_suggestion_api_returns_top_k_without_creating_normalization_candidate():
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/skills",
        json={"skill_name": "Python", "category": "编程语言", "aliases": ["Py"]},
        headers=headers,
    )
    assert created.status_code == 200
    before = client.get("/api/v1/skills/normalize-candidates", headers=headers).json()[
        "data"
    ]

    response = client.post(
        "/api/v1/skills/normalization-suggestions",
        json={"raw_skill": "py", "context": "Python backend", "top_k": 1},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    suggestion = response.json()["data"][0]
    assert suggestion["skill_name"] == "Python"
    assert suggestion["matched_alias"] == "Py"
    assert suggestion["semantic_available"] is False
    after = client.get("/api/v1/skills/normalize-candidates", headers=headers).json()[
        "data"
    ]
    assert after == before == []


def test_semantic_available_does_not_disable_above_max_semantic_candidates():
    skills = [
        skill(f"skill-{index:04d}", f"Skill {index:04d}")
        for index in range(5001)
    ] + [skill("llmops", "LLMOps")]
    results = rank_normalization_suggestions(
        raw_skill="LLMOps",
        context="大模型平台运维",
        skills=skills,
        aliases=[],
        reviewed_skill_id=None,
        top_k=5,
        embedding=DualRecallEmbedding(),
        max_semantic_candidates=5000,
    )
    assert results[0].skill_id == "llmops"
    assert results[0].semantic_available is True
