from __future__ import annotations

from collections.abc import Callable

from jobgraph_contracts.deepseek import (
    DeepSeekClient,
    DeepSeekClientError,
    DeepSeekConnectionError,
    DeepSeekHTTPStatusError,
    DeepSeekRateLimitError,
    DeepSeekTimeoutError,
    InvalidJSONError,
    MissingAPIKeyError,
)

from app.contexts.evidence_rag.contracts import (
    EvidenceRagError,
    EvidenceRagLlmAnswer,
    EvidenceEntailment,
    EvidenceQueryDepth,
)


DeepSeekClientFactory = Callable[[], DeepSeekClient]

SYSTEM_PROMPT = (
    "你是 JobGraph 的 Evidence RAG 回答器。\n"
    "只能依据提供的 Evidence 回答问题。允许基于 Evidence 做明确、低风险、确定性的语义与逻辑推断，"
    "不要求答案必须在原文中逐字出现。\n"
    "允许的推断包括：明确的学历层级、数值大小、时间先后、集合包含和条件关系；"
    "多条 Evidence 可以直接组合得到的结论；极基础且稳定的常识关系。\n"
    "禁止：根据岗位名称猜测技能；根据技能存在推测熟练程度；根据经历推测能力水平；"
    "将“未提及”理解为“不存在”；使用 Evidence 之外的具体事实、专业知识或猜测补全答案；"
    "在 Evidence 存在关键冲突时自行选择其中一条。\n"
    "判断标准：如果答案可以由 Evidence 直接得到，或通过上述确定性推断得到，则 supported=true。"
    "如果仍需要额外假设、猜测或外部知识，则 supported=false。\n"
    "如果问题只能部分回答，可以明确说明哪些部分有依据、哪些部分无法判断；"
    "只要回答中的陈述都受到 Evidence 支持，可以设置 supported=true。\n"
    "used_evidence_ids 只能填写真正用于回答的 Evidence 中已有 evidence_id。\n"
    "supported=false 时，used_evidence_ids 必须为 []。\n"
    "回答使用中文，简洁直接，不输出推理过程。\n"
    "只输出合法 JSON object：\n"
    "{\"answer\":\"...\",\"supported\":true,\"used_evidence_ids\":[\"evidence_id\"]}"
)

ENTAILMENT_SYSTEM_PROMPT = (
    "你是 JobGraph 的 Evidence Grounding / Entailment 判定器。\n"
    "任务：先识别用户问题中需要判断的具体条件或命题，再逐条判定每条 Evidence 与该命题的关系。\n"
    "关系只能是：\n"
    "support: Evidence 能直接支持该命题；\n"
    "contradict: Evidence 与该命题冲突；\n"
    "insufficient: Evidence 相关，但无法推出该命题。\n"
    "关键规则：必须区分条件维度（学历、工作经验、技能、熟练度、专业、证书、年龄等）；"
    "一个维度上的放宽不得用于推导另一个维度上的放宽。"
    "例如“接受无经验”只能支持缺少工作经验，不能支持缺少编程能力；"
    "“接受转行”不蕴含没有技能基础；“不要求证书”不等于“不要求能力”。\n"
    "Evidence 与问题主题相关并不代表它支持结论。存在 contradiction 时必须判定为 contradict，不得忽略。\n"
    "只输出合法 JSON object：\n"
    "{\"claims\":[{\"claim\":\"...\",\"relation\":\"support|contradict|insufficient\","
    "\"used_evidence_ids\":[\"...\"],\"reason\":\"...\",\"dimension\":\"...\"}]}"
)

COMBINED_SYSTEM_PROMPT = (
    "你是 JobGraph 的 Evidence Grounding / Entailment 判定器和回答器。\n"
    "第一步：先识别用户问题中需要判断的具体条件或命题，再逐条判定每条 Evidence 与该命题的关系。\n"
    "关系只能是：\n"
    "support: Evidence 能直接支持该命题；\n"
    "contradict: Evidence 与该命题冲突；\n"
    "insufficient: Evidence 相关，但无法推出该命题。\n"
    "关键规则：必须区分条件维度（学历、工作经验、技能、熟练度、专业、证书、年龄等）；"
    "一个维度上的放宽不得用于推导另一个维度上的放宽。"
    "例如“接受无经验”只能支持缺少工作经验，不能支持缺少编程能力；"
    "“接受转行”不蕴含没有技能基础；“不要求证书”不等于“不要求能力”。\n"
    "Evidence 与问题主题相关并不代表它支持结论。存在 contradiction 时必须判定为 contradict，不得忽略。\n"
    "第二步：只根据上述关系生成最终回答。存在 contradiction 或没有任何 support 时 supported 必须为 false，"
    "并明确说明哪些证据支持、哪些证据矛盾或不足；不得给出肯定结论。"
    "used_evidence_ids 只能填写被判定为 support 的 Evidence。\n"
    "回答应先给出直接结论，再按问题需要解释关键差异、限制和无法判断的部分；"
    "多岗位问题需分别说明各岗位，避免只给一句笼统结论，也不要大段复述 Evidence 原文。\n"
    "回答长度应按问题复杂度自适应：简单事实题简短直接，复杂/比较题按关键维度展开，"
    "概览/综合题充分利用多条互补 Evidence，给出结构化、信息密度高的回答；"
    "不得为追求长回答而堆砌，也不得因为追求简洁而漏掉 Evidence 明确支持的重要信息。\n"
    "只输出合法 JSON object：\n"
    "{\"claims\":[{\"claim\":\"...\",\"relation\":\"support|contradict|insufficient\","
    "\"used_evidence_ids\":[\"...\"],\"reason\":\"...\",\"dimension\":\"...\"}],"
    "\"answer\":{\"answer\":\"...\",\"supported\":true,\"used_evidence_ids\":[\"...\"]}}"
)

QUERY_DEPTH_RESPONSE_GUIDANCE: dict[EvidenceQueryDepth, str] = {
    "fact": (
        "当前问题属于简单事实或单条件判断，回答控制在 1~3 句话：先给直接结论，"
        "再补充 Evidence 明确支持的限制、例外或关键信息。不要为了显得完整而扩展。"
    ),
    "compare": (
        "当前问题属于比较或多条件问题，回答按关键维度展开，逐项说明各岗位/对象的差异；"
        "只陈述 Evidence 支持的差异，不重复原文，以信息密度优先，避免模板化扩写。"
    ),
    "overview": (
        "当前问题属于概览、总结、趋势或岗位能力分析，应综合多条互补 Evidence 后给出"
        "结构化、信息密度高的回答；有多条互补 Evidence 时尽量综合利用，不要只依赖一条就结束，"
        "但仍以能支持回答的 Evidence 为准，不机械追求长回答。"
    ),
}


def _combined_system_prompt(query_depth: EvidenceQueryDepth) -> str:
    guidance = QUERY_DEPTH_RESPONSE_GUIDANCE.get(
        query_depth, QUERY_DEPTH_RESPONSE_GUIDANCE["fact"]
    )
    return COMBINED_SYSTEM_PROMPT + "\n\n回答深度要求：\n" + guidance


def _multi_object_prompt(
    business_object_ids: tuple[str, ...],
    objects_with_evidence: tuple[str, ...],
) -> str:
    evidence_objects = set(objects_with_evidence)
    object_lines = "\n".join(
        (
            f"- {object_id}："
            + (
                "有可见 Evidence，必须单独总结其核心要求或特点。"
                if object_id in evidence_objects
                else "当前没有足够可见 Evidence，必须明确写‘现有证据不足’，不得用其他岗位证据代替。"
            )
        )
        for object_id in business_object_ids
    )
    return (
        "当前是多岗位比较。用户选择的每个岗位都必须在回答中逐项涉及，不能无提示遗漏；"
        "完成逐项说明后，才可以总结主要差异。\n"
        "岗位覆盖要求：\n"
        + object_lines
    )


class DeepSeekEvidenceRagLlm:
    def __init__(
        self,
        *,
        model: str,
        algorithm_version: str,
        timeout_seconds: int = 120,
        client_factory: DeepSeekClientFactory | None = None,
    ) -> None:
        if not model.strip() or not algorithm_version.strip():
            raise ValueError("DeepSeek model and algorithm version are required")
        if timeout_seconds <= 0:
            raise ValueError("DeepSeek timeout must be positive")
        self._model = model
        self._algorithm_version = algorithm_version
        self._client_factory = client_factory or (
            lambda: DeepSeekClient(model, timeout=timeout_seconds)
        )

    def entailment(
        self, *, query_text: str, evidence_text: str
    ) -> tuple[EvidenceEntailment, ...]:
        user_prompt = f"查询：{query_text}\n\nEvidence：\n{evidence_text}"
        result = self._extract(ENTAILMENT_SYSTEM_PROMPT, user_prompt)
        return self._parse_entailments(result.data)

    def judge_and_answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        query_depth: EvidenceQueryDepth = "fact",
        business_object_ids: tuple[str, ...] = (),
        objects_with_evidence: tuple[str, ...] = (),
    ) -> tuple[tuple[EvidenceEntailment, ...], EvidenceRagLlmAnswer]:
        user_prompt = f"查询：{query_text}\n\nEvidence：\n{evidence_text}"
        system_prompt = _combined_system_prompt(query_depth)
        if business_object_ids:
            system_prompt += "\n\n" + _multi_object_prompt(
                business_object_ids, objects_with_evidence
            )
        result = self._extract(system_prompt, user_prompt)
        return (
            self._parse_entailments(result.data),
            self._parse_answer(result.data),
        )

    @staticmethod
    def _parse_entailments(data: dict) -> tuple[EvidenceEntailment, ...]:
        claims_raw = data.get("claims")
        if not isinstance(claims_raw, list) or not claims_raw:
            raise EvidenceRagError(
                "LLM_RESPONSE_INVALID",
                "DeepSeek entailment must return a non-empty claims array",
            )
        claims: list[EvidenceEntailment] = []
        for item in claims_raw:
            if not isinstance(item, dict):
                raise EvidenceRagError(
                    "LLM_RESPONSE_INVALID",
                    "DeepSeek entailment claim must be an object",
                )
            relation = item.get("relation")
            if relation not in {"support", "contradict", "insufficient"}:
                raise EvidenceRagError(
                    "LLM_RESPONSE_INVALID",
                    "DeepSeek entailment relation is invalid",
                )
            used_raw = item.get("used_evidence_ids")
            if not isinstance(used_raw, list):
                raise EvidenceRagError(
                    "LLM_RESPONSE_INVALID",
                    "DeepSeek entailment used_evidence_ids must be an array",
                )
            used = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in used_raw
                    if isinstance(value, str) and value.strip()
                )
            )
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise EvidenceRagError(
                    "LLM_RESPONSE_INVALID",
                    "DeepSeek entailment reason must be a non-empty string",
                )
            dimension = item.get("dimension")
            try:
                claims.append(
                    EvidenceEntailment(
                        claim=str(item.get("claim") or "").strip(),
                        relation=relation,
                        used_evidence_ids=used,
                        reason=reason.strip(),
                        dimension=(
                            str(dimension).strip()
                            if isinstance(dimension, str) and dimension.strip()
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise EvidenceRagError(
                    "LLM_RESPONSE_INVALID",
                    f"DeepSeek entailment claim is invalid: {exc}",
                ) from exc
        return tuple(claims)

    def answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        entailment_text: str | None = None,
    ) -> EvidenceRagLlmAnswer:
        user_prompt = f"查询：{query_text}\n\nEvidence：\n{evidence_text}"
        if entailment_text:
            user_prompt += f"\n\nEntailment 判定：\n{entailment_text}\n"
            user_prompt += (
                "回答必须基于上述 Entailment 判定。"
                "存在 contradiction 或没有任何 support 时 supported 必须为 false，"
                "并明确说明哪些证据支持、哪些证据矛盾或不足；不得给出肯定结论。"
                "used_evidence_ids 只能填写被判定为 support 的 Evidence。"
            )
        result = self._extract(SYSTEM_PROMPT, user_prompt)
        return self._parse_answer(result.data)

    @staticmethod
    def _parse_answer(data: dict) -> EvidenceRagLlmAnswer:
        raw = data.get("answer")
        if not isinstance(raw, dict):
            raise EvidenceRagError(
                "LLM_RESPONSE_INVALID",
                "DeepSeek RAG answer must be an object",
            )
        answer_text = raw.get("answer")
        supported = raw.get("supported")
        used_raw = raw.get("used_evidence_ids")
        if not isinstance(answer_text, str) or not answer_text.strip():
            raise EvidenceRagError(
                "LLM_RESPONSE_INVALID",
                "DeepSeek RAG answer must be a non-empty string",
            )
        if not isinstance(supported, bool):
            raise EvidenceRagError(
                "LLM_RESPONSE_INVALID",
                "DeepSeek RAG supported must be a boolean",
            )
        if not isinstance(used_raw, list):
            raise EvidenceRagError(
                "LLM_RESPONSE_INVALID",
                "DeepSeek RAG used_evidence_ids must be an array",
            )
        used = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in used_raw
                if isinstance(value, str) and value.strip()
            )
        )
        return EvidenceRagLlmAnswer(
            answer=answer_text.strip(),
            supported=supported,
            used_evidence_ids=used,
        )

    def _extract(self, system_prompt: str, user_prompt: str):
        try:
            return self._client_factory().extract(system_prompt, user_prompt)
        except MissingAPIKeyError as exc:
            raise EvidenceRagError("LLM_API_KEY_MISSING", str(exc)) from exc
        except DeepSeekTimeoutError as exc:
            raise EvidenceRagError("LLM_TIMEOUT", str(exc)) from exc
        except DeepSeekConnectionError as exc:
            raise EvidenceRagError("LLM_CONNECTION_FAILED", str(exc)) from exc
        except DeepSeekRateLimitError as exc:
            raise EvidenceRagError("LLM_RATE_LIMITED", str(exc)) from exc
        except DeepSeekHTTPStatusError as exc:
            code = (
                "LLM_AUTH_FAILED"
                if getattr(exc, "status_code", None) in {401, 403}
                else "LLM_PROVIDER_UNAVAILABLE"
            )
            raise EvidenceRagError(code, str(exc)) from exc
        except InvalidJSONError as exc:
            raise EvidenceRagError("LLM_RESPONSE_INVALID", str(exc)) from exc
        except DeepSeekClientError as exc:
            raise EvidenceRagError(
                getattr(exc, "code", "LLM_PROVIDER_UNAVAILABLE"), str(exc)
            ) from exc

    def provider(self) -> str:
        return "deepseek"

    def model(self) -> str:
        return self._model

    def model_version(self) -> str:
        return self._algorithm_version


__all__ = ["DeepSeekEvidenceRagLlm"]
