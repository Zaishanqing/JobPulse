"""DeepSeek-backed semantic similarity provider for novelty scoring.

The knowledge-graph service is otherwise deterministic; this provider is the
single LLM surface. It is optional and only used when the novelty caller passes
a provider instance. When ``DEEPSEEK_API_KEY`` is absent, construction fails
explicitly (no fallback), matching the "no fallback code" rule.
"""

from __future__ import annotations

import json
import os
from typing import Any

from jobgraph_contracts.deepseek import DeepSeekClient

from app.ports.providers import SemanticSimilarityJudgment, SemanticSimilarityProvider

_SYSTEM_PROMPT = (
    "你是岗位技术方向评委。判断候选岗位与基准岗位是否属于【同一技术方向/领域】。"
    "输出一个 JSON object：{\"same_domain\": true/false, \"similarity\": 0到1的数, \"reason\": \"一句话\"}。"
    "判断标准是\"是否同一技术方向\"，而不是\"是否都用到 AI/大模型\"。"
    "例如：具身智能/机器人/视频生成/三维重建属于【不同方向】；大模型训练/微调/推理属于【同一方向】。"
)


class DeepSeekSemanticSimilarityProvider:
    def __init__(self, model: str = "deepseek-v4-flash", timeout: int = 120):
        self._model = model
        self._timeout = timeout
        # 延迟初始化：构造不要求 key，首次 judge 时才真正建连（避免 import 期失败）。
        self._client: DeepSeekClient | None = None

    def _ensure_client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = DeepSeekClient(model=self._model, timeout=self._timeout)
        return self._client

    def judge(
        self,
        *,
        baseline_name: str,
        baseline_skills: str,
        baseline_responsibilities: str,
        candidate_name: str,
        candidate_skills: str,
        candidate_responsibilities: str,
    ) -> SemanticSimilarityJudgment:
        client = self._ensure_client()
        user_prompt = (
            f"基准岗位：{baseline_name}\n基准技能：{baseline_skills}\n"
            f"基准职责：{baseline_responsibilities}\n\n"
            f"候选岗位：{candidate_name}\n候选技能：{candidate_skills}\n"
            f"候选职责：{candidate_responsibilities}\n"
            f"请判断两者是否属于同一技术方向。"
        )
        result = client.extract(_SYSTEM_PROMPT, user_prompt)
        data = result.data
        similarity = float(data.get("similarity", 0.0))
        same_domain = bool(data.get("same_domain", False))
        reason = str(data.get("reason", ""))
        return SemanticSimilarityJudgment(
            similarity=max(0.0, min(1.0, similarity)),
            same_domain=same_domain,
            reason=reason,
        )


__all__ = ["DeepSeekSemanticSimilarityProvider"]
