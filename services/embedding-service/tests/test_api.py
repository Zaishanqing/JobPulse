from __future__ import annotations

import math

from fastapi.testclient import TestClient

from app.backend import BgeM3Backend
from app.config import Settings
from app.main import create_app


class FakeBackend:
    def encode(self, inputs, *, normalize):
        vector = (1.0,) + (0.0,) * 1023
        return tuple(vector for _ in inputs)


def _settings(**updates) -> Settings:
    values = {
        "model_revision": "a" * 40,
        "max_input_count": 2,
        "max_text_chars": 20,
    }
    values.update(updates)
    return Settings(**values)


def test_embedding_api_is_batched_versioned_and_stable() -> None:
    app = create_app(_settings(), backend_loader=lambda _settings: FakeBackend())
    with TestClient(app) as client:
        first = client.post(
            "/v1/embeddings", json={"inputs": ["Python", "Redis"], "normalize": True}
        )
        repeated = client.post(
            "/v1/embeddings", json={"inputs": ["Python", "Redis"], "normalize": True}
        )
        model = client.get("/v1/models/current")
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200

    assert first.status_code == 200
    assert first.json()["vectors"] == repeated.json()["vectors"]
    assert first.json()["model_revision"] == "a" * 40
    assert first.json()["dimension"] == 1024
    assert first.json()["usage"] == {"input_count": 2, "character_count": 11}
    assert model.json()["representation"] == "dense"
    assert model.json()["device"] == "cpu"
    assert model.json()["use_fp16"] is False


def test_cuda_fp32_configuration_is_visible_in_model_contract() -> None:
    app = create_app(
        _settings(device="cuda", use_fp16=False),
        backend_loader=lambda _settings: FakeBackend(),
    )
    with TestClient(app) as client:
        model = client.get("/v1/models/current")

    assert model.json()["device"] == "cuda"
    assert model.json()["use_fp16"] is False


def test_invalid_inputs_have_explicit_sanitized_errors() -> None:
    app = create_app(_settings(), backend_loader=lambda _settings: FakeBackend())
    with TestClient(app) as client:
        empty = client.post("/v1/embeddings", json={"inputs": [" "]})
        long = client.post("/v1/embeddings", json={"inputs": ["x" * 21]})
        batch = client.post("/v1/embeddings", json={"inputs": ["a", "b", "c"]})

    assert empty.status_code == 422
    assert empty.json()["code"] == "EMBEDDING_REQUEST_INVALID"
    assert "inputs" in empty.json()["message"]
    assert long.json()["code"] == "EMBEDDING_TEXT_TOO_LONG"
    assert batch.json()["code"] == "EMBEDDING_BATCH_TOO_LARGE"


class InvalidBackend:
    def encode(self, inputs, *, normalize):
        return ((math.nan,) + (0.0,) * 1023,)


def test_non_finite_model_output_is_rejected() -> None:
    app = create_app(_settings(), backend_loader=lambda _settings: InvalidBackend())
    with TestClient(app) as client:
        response = client.post("/v1/embeddings", json={"inputs": ["Python"]})

    assert response.status_code == 500
    assert response.json()["code"] == "EMBEDDING_OUTPUT_INVALID"


class ArrayLikeDenseOutput:
    def tolist(self):
        return [[3.0, 4.0], [0.0, 5.0]]


class ArrayLikeModel:
    def encode(self, inputs, **_kwargs):
        assert len(inputs) == 2
        return {"dense_vecs": ArrayLikeDenseOutput()}


def test_bge_backend_accepts_array_like_dense_output() -> None:
    backend = object.__new__(BgeM3Backend)
    backend._model = ArrayLikeModel()
    backend._batch_size = 2
    backend._dimension = 2

    vectors = backend.encode(("first", "second"), normalize=True)

    assert vectors == ((0.6, 0.8), (0.0, 1.0))
