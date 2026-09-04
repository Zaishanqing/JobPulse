from __future__ import annotations

import copy
import math
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from app.application import responsibility_ce as responsibility_ce_module
from app.application.responsibility_ce import (
    ResponsibilityCEConfig,
    ResponsibilityCEVerifier,
)
from app.bootstrap import application as bootstrap_application
from app.domain.context_matching import ContextMatchingConfig
from app.domain.degree_levels import parse_degree_from_text
from app.domain.profiles import CVMatchProfile, MatchFeature, PositionMatchProfile


def _config(tmp_path, **updates) -> ResponsibilityCEConfig:
    model_path = tmp_path / "responsibility-ce"
    model_path.mkdir(exist_ok=True)
    values = {
        "model_path": str(model_path),
        "threshold": 1.0,
        "top_k": 2,
        "fallback_to_rules": False,
    }
    values.update(updates)
    return ResponsibilityCEConfig(**values)


def _verifier(tmp_path, **config_updates) -> ResponsibilityCEVerifier:
    verifier = object.__new__(ResponsibilityCEVerifier)
    verifier.config = _config(tmp_path, **config_updates)
    verifier._model = object()
    verifier._tokenizer = object()
    verifier._model_load_error = None
    verifier._device = "cpu"
    verifier.last_fallback = None
    verifier.last_retrieval = None
    return verifier


def test_tokenizer_loader_enables_mistral_regex_fix(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeTokenizer:
        @staticmethod
        def from_pretrained(model_path, **kwargs):
            calls["tokenizer"] = (model_path, kwargs)
            return object()

    class FakeModel:
        def to(self, device):
            calls["device"] = device

        def eval(self):
            calls["eval"] = True

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(model_path, **kwargs):
            calls["model"] = (model_path, kwargs)
            return FakeModel()

    torch_module = ModuleType("torch")
    torch_module.cuda = SimpleNamespace(is_available=lambda: False)
    transformers_module = ModuleType("transformers")
    transformers_module.AutoTokenizer = FakeTokenizer
    transformers_module.AutoModelForSequenceClassification = FakeAutoModel
    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "transformers", transformers_module)

    verifier = ResponsibilityCEVerifier(_config(tmp_path))

    assert verifier.model_loaded is True
    _, tokenizer_kwargs = calls["tokenizer"]
    assert tokenizer_kwargs == {
        "local_files_only": True,
        "fix_mistral_regex": True,
    }


def _profiles(cv_payload, position_payload, *, with_candidate: bool = True):
    cv_data = copy.deepcopy(cv_payload)
    if with_candidate:
        cv_data["work_experiences"] = [
            {
                "experience_id": "exp:backend",
                "kind": "work",
                "role": "backend engineer",
                "responsibilities": ["设计并维护高可用后端服务"],
                "business_scenarios": [],
                "tool_skill_ids": [],
                "start_date": "2022-01-01",
                "end_date": "2025-01-01",
                "evidence_refs": [
                    {
                        "source_id": "cv:responsibility:1",
                        "quote": "设计并维护高可用后端服务",
                        "start": 0,
                        "end": 12,
                        "alignment": "exact",
                        "occurrence_index": 0,
                    }
                ],
            }
        ]
    position_data = copy.deepcopy(position_payload)
    position_data["core_responsibilities"] = ["负责后端服务设计与可靠性"]
    position_data["evidence_refs"].append(
        {
            "source_id": "jd:responsibility:1",
            "quote": "负责后端服务设计与可靠性",
            "start": 0,
            "end": 12,
            "alignment": "exact",
            "occurrence_index": 0,
        }
    )
    return (
        CVMatchProfile.model_validate(cv_data),
        PositionMatchProfile.model_validate(position_data),
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"threshold": -0.1}, "threshold must be non-negative"),
        ({"top_k": 0}, "top_k must be at least 1"),
    ],
)
def test_config_rejects_invalid_threshold_and_top_k(tmp_path, updates, message) -> None:
    with pytest.raises(ValueError, match=message):
        _config(tmp_path, **updates)


def test_config_rejects_missing_model_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="model path does not exist"):
        ResponsibilityCEConfig(model_path=str(tmp_path / "missing"))


def test_frozen_ce_threshold_and_verification_parameters_are_unchanged(tmp_path) -> None:
    model_path = tmp_path / "responsibility-ce"
    model_path.mkdir()
    config = ResponsibilityCEConfig(model_path=str(model_path))
    assert config.threshold == 1.098377
    assert config.top_k == 8
    assert config.max_length == 256
    assert config.embedding_model == "BAAI/bge-m3"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("岗位学历不限", None),
        ("本科及以上，硕士优先", "master"),
        ("Ph.D. or postdoctoral experience", "postdoc"),
        ("高中或大专学历", "associate"),
    ],
)
def test_parse_degree_from_text_returns_highest_level(text, expected) -> None:
    assert parse_degree_from_text(text) == expected


def test_dense_topk_ranks_real_embedding_response(tmp_path, monkeypatch) -> None:
    verifier = _verifier(tmp_path, top_k=1)
    candidates = [
        ("exp:1", "弱相关", ()),
        ("exp:2", "强相关", ()),
    ]

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"vectors": [[1.0, 0.0], [0.1, 0.0], [0.9, 0.0]]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    ranked = verifier._dense_topk("后端可靠性", candidates)
    assert ranked is not None
    assert [(item[0], item[3]) for item in ranked] == [("exp:2", 0.9)]


@pytest.mark.parametrize("failure", ["http", "status", "length"])
def test_dense_topk_reports_dependency_failure(tmp_path, monkeypatch, failure) -> None:
    verifier = _verifier(tmp_path)

    class Response:
        status_code = 503 if failure == "status" else 200

        @staticmethod
        def json():
            vectors = [[1.0], [0.8]]
            if failure == "length":
                vectors.pop()
            return {"vectors": vectors}

    def post(*args, **kwargs):
        if failure == "http":
            raise httpx.ConnectError("embedding unavailable")
        return Response()

    monkeypatch.setattr(httpx, "post", post)
    assert verifier._dense_topk("职责", [("exp:1", "经历", ())]) is None


@pytest.mark.parametrize(
    ("score", "status", "reason"),
        [
            (2.0, "matched", "RESPONSIBILITY_MATCHED"),
            (0.5, "not_observed", "RESPONSIBILITY_NOT_OBSERVED"),
        ],
)
def test_verify_maps_ce_score_to_business_result(
    tmp_path,
    monkeypatch,
    cv_payload,
    position_payload,
    score,
    status,
    reason,
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload)
    monkeypatch.setattr(
        verifier,
        "_dense_topk_many",
        lambda requirements, candidates: [[(*candidates[0], 0.95, 1)] for _ in requirements],
    )
    monkeypatch.setattr(verifier, "_ce_scores", lambda requirement, candidates: [score])

    result = verifier.verify(cv, position, object())[0]

    assert result.match_status == status
    assert result.reason_code == reason
    if status == "matched":
        assert result.candidate_experience_id == "exp:backend"
        assert result.confidence == round(1.0 / (1.0 + math.exp(-score)), 6)
        assert result.position_evidence[0].source_id == "jd:responsibility:1"
    else:
        assert result.status_detail is None
        assert result.candidate_experience_id is None
        assert result.ce_score == score
        assert result.threshold_margin == round(score - verifier.config.threshold, 6)
        assert result.top_candidates
    assert verifier.last_retrieval == {
        "top_k": 2,
        "threshold": 1.0,
        "embedding_model": "BAAI/bge-m3",
        "embedding_revision": "5617a9f61b028005a4858fdac845db406aefb181",
    }


def test_verify_returns_not_observed_when_cv_has_no_responsibilities(
    tmp_path, cv_payload, position_payload
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload, with_candidate=False)
    result = verifier.verify(cv, position, object())[0]
    assert result.match_status == "not_observed"
    assert result.reason_code == "RESPONSIBILITY_NOT_OBSERVED"
    assert result.candidate_evidence == ()


def test_verify_fails_closed_when_model_or_embedding_is_unavailable(
    tmp_path, monkeypatch, cv_payload, position_payload
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload)
    verifier._model = None
    verifier._model_load_error = "missing weights"
    with pytest.raises(RuntimeError, match="model unavailable"):
        verifier.verify(cv, position, object())

    verifier._model = object()
    monkeypatch.setattr(verifier, "_dense_topk_many", lambda requirements, candidates: None)
    with pytest.raises(RuntimeError, match="dense retrieval unavailable"):
        verifier.verify(cv, position, object())


@pytest.mark.parametrize("dependency", ["model", "embedding"])
def test_verify_uses_configured_rule_fallback(
    tmp_path, monkeypatch, cv_payload, position_payload, dependency
) -> None:
    verifier = _verifier(tmp_path, fallback_to_rules=True)
    cv, position = _profiles(cv_payload, position_payload)
    if dependency == "model":
        verifier._model = None
        verifier._model_load_error = "missing weights"
    else:
        monkeypatch.setattr(verifier, "_dense_topk_many", lambda requirements, candidates: None)
    monkeypatch.setattr(
        responsibility_ce_module,
        "evaluate_responsibilities",
        lambda cv_profile, position_profile, config: (),
    )

    assert verifier.verify(cv, position, object()) == ()
    assert verifier.last_fallback == "RULE_FALLBACK"


def test_verify_batches_requirements_and_reuses_exact_result(
    tmp_path, monkeypatch, cv_payload, position_payload
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload)
    position = position.model_copy(
        update={
            "core_responsibilities": (
                "负责后端服务设计与可靠性",
                "负责服务性能优化",
            )
        }
    )
    requests = []

    class Response:
        status_code = 200

        def __init__(self, inputs):
            self._inputs = inputs

        def json(self):
            vectors = []
            for index, _ in enumerate(self._inputs):
                vectors.append([1.0, float(index)])
            return {"vectors": vectors}

    def post(*_args, **kwargs):
        inputs = kwargs["json"]["inputs"]
        requests.append(inputs)
        return Response(inputs)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(
        verifier,
        "_ce_scores",
        lambda requirement, candidates: [2.0 for _ in candidates],
    )

    first = verifier.verify(cv, position, object())
    repeated = verifier.verify(cv, position, object())

    assert first == repeated
    assert len(requests) == 1
    assert requests[0] == [
        "负责后端服务设计与可靠性",
        "负责服务性能优化",
        "设计并维护高可用后端服务",
    ]
    assert verifier.verification_cache_hits == 1
    assert verifier.verification_cache_misses == 1


def test_verify_consumes_context_candidate_mode(
    tmp_path, monkeypatch, cv_payload, position_payload
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload)
    work = cv.work_experiences[0].model_copy(
        update={"experience_id": "work_001"}
    )
    feature = MatchFeature(
        feature_id="feature_responsibility_mode",
        document_id=cv.cv_id,
        side="cv",
        feature_type="task",
        source_object_id="work_001",
        source_scope="work:work_001:responsibility",
        raw_text="设计并维护高可用后端服务",
        resolution_status="resolved",
        taxonomy_version=cv.taxonomy_version,
        derivation_version="cv-match-feature.v1",
    )
    cv = cv.model_copy(
        update={
            "work_experiences": (work,),
            "match_features": cv.match_features + (feature,),
        }
    )

    monkeypatch.setattr(
        verifier,
        "_dense_topk_many",
        lambda requirements, candidates: [
            [(*candidates[0], 0.95, 1)] for _ in requirements
        ],
    )
    monkeypatch.setattr(
        verifier,
        "_ce_scores",
        lambda requirement, candidates: [2.0 for _ in candidates],
    )

    structured = verifier.verify(
        cv,
        position,
        ContextMatchingConfig(responsibility_candidate_mode="structured_sentence"),
    )
    features = verifier.verify(
        cv,
        position,
        ContextMatchingConfig(responsibility_candidate_mode="match_feature"),
    )

    assert structured[0].match_status == "matched"
    assert verifier.last_candidate_mode == "match_feature"
    assert verifier.last_candidate_count == 1
    assert verifier.last_candidate_sources == ("match_feature",)
    assert features[0].match_status == "matched"


def test_verify_runs_when_context_matching_is_disabled(
    tmp_path, monkeypatch, cv_payload, position_payload
) -> None:
    verifier = _verifier(tmp_path)
    cv, position = _profiles(cv_payload, position_payload)
    monkeypatch.setattr(
        verifier,
        "_dense_topk_many",
        lambda requirements, candidates: [
            [(*candidates[0], 0.95, 1)] for _ in requirements
        ],
    )
    monkeypatch.setattr(
        verifier,
        "_ce_scores",
        lambda requirement, candidates: [2.0 for _ in candidates],
    )

    result = verifier.verify(
        cv,
        position,
        ContextMatchingConfig(
            context_matching_enabled=False,
            responsibility_candidate_mode="combined",
        ),
    )

    assert result
    assert verifier.last_candidate_mode == "combined"
    assert verifier.last_candidate_count > 0
    assert result[0].top_candidates


def test_embedding_cache_only_fetches_new_exact_texts(tmp_path, monkeypatch) -> None:
    verifier = _verifier(tmp_path)
    requests = []

    class Response:
        status_code = 200

        def __init__(self, inputs):
            self._inputs = inputs

        def json(self):
            return {"vectors": [[float(index + 1), 0.0] for index, _ in enumerate(self._inputs)]}

    def post(*_args, **kwargs):
        inputs = kwargs["json"]["inputs"]
        requests.append(inputs)
        return Response(inputs)

    monkeypatch.setattr(httpx, "post", post)

    assert verifier._embedding_vectors(["职责A", "经历A"]) is not None
    assert verifier._embedding_vectors(["职责A", "经历B"]) is not None

    assert requests == [["职责A", "经历A"], ["经历B"]]
    assert verifier.embedding_cache_hits == 1
    assert verifier.embedding_cache_misses == 3


def test_bootstrap_injects_enabled_responsibility_ce_fail_closed(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "responsibility-ce"
    model_path.mkdir()

    class FakeVerifier:
        def __init__(self, config):
            self.config = config
            self.model_loaded = True

    monkeypatch.setattr(bootstrap_application, "ResponsibilityCEVerifier", FakeVerifier)
    app = bootstrap_application.create_app(
        runtime_env={
            "MATCHING_RUNTIME_MODE": "test",
            "MATCHING_RESPONSIBILITY_CE_MODE": "enabled",
            "MATCHING_RESPONSIBILITY_CE_MODEL_PATH": str(model_path),
            "MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL": "http://embedding:8000",
        }
    )

    assert app.state.responsibility_ce_mode == "enabled"
    assert app.state.responsibility_ce_verifier.config.fallback_to_rules is False
    assert (
        app.state.match_evaluation_service._responsibility_verifier
        is app.state.responsibility_ce_verifier
    )
