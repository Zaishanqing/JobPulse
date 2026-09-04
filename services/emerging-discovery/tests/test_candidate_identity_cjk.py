from __future__ import annotations

from app.domain.candidate_identity import _bigrams, _tokens


def test_cjk_tokens_are_per_segment_and_do_not_cross_boundaries():
    tokens = _tokens("机器学习 / 大模型")

    # 每个独立 CJK segment 内生成 bigram
    assert "机器" in tokens
    assert "器学" in tokens
    assert "学习" in tokens
    assert "大模" in tokens
    assert "模型" in tokens
    # 严禁跨标点/空白生成不存在的边界 token
    assert "习大" not in tokens


def test_cjk_bigrams_are_per_segment_and_do_not_cross_boundaries():
    bigrams = _bigrams("机器学习 / 大模型")

    assert "学习" in bigrams
    assert "大模" in bigrams
    assert "模型" in bigrams
    assert "习大" not in bigrams


def test_ascii_token_behavior_remains_compatible():
    tokens = _tokens("RAG + Python")

    assert "rag" in tokens
    assert "python" in tokens
    assert "rag+python" not in tokens


def test_ascii_bigrams_keep_previous_concatenated_behaviour():
    # 历史 ASCII 行为是先把空白去掉再整串生成 bigram，
    # “build training” 必须仍保留跨词边界 bigram “dt”。
    bigrams = _bigrams("build training")
    assert "dt" in bigrams
    assert "bu" in bigrams
    assert "ng" in bigrams
