from __future__ import annotations

from typing import Mapping, Sequence

from app.contexts.evidence_independence.application import build_summary
from app.contexts.evidence_independence.contracts import (
    ConclusionRecomputePort,
    ConclusionScore,
    EvidenceRecord,
    IndependenceRequest,
)


EVIDENCE_SUPPORT_SCORE_PROVIDER = "evidence-support-score.v2"
EVIDENCE_SUPPORT_SCORE_PROVIDER_ALIAS = "evidence-support-score"
EVIDENCE_SCORE_PROVIDER = EVIDENCE_SUPPORT_SCORE_PROVIDER
EVIDENCE_SCORE_PROVIDER_ALIAS = EVIDENCE_SUPPORT_SCORE_PROVIDER_ALIAS


class EvidenceSupportScoreConclusion(ConclusionRecomputePort):
    """Evidence-support conclusion used by the CF-01 certificate.

    This is an evidence-support conclusion only: the cluster-level effective
    sample size gated by the UNC-01 state. It answers whether evidence support
    survives source/enterprise/template/window removal, not whether a business
    conclusion such as a Trend or Emerging result survives. Business conclusion
    adapters are connected through the InsightCard layer.
    """

    provider = EVIDENCE_SUPPORT_SCORE_PROVIDER

    def evaluate(
        self,
        records: Sequence[EvidenceRecord],
        request: IndependenceRequest,
    ) -> ConclusionScore:
        summary = build_summary(records, request)
        return ConclusionScore(
            score=round(summary.effective_sample_size, 4),
            state=summary.uncertainty_state,
            rank=1,
            threshold_crossed=summary.uncertainty_state != "ok",
            failure_reasons=summary.uncertainty_reasons,
        )


_EVIDENCE_SUPPORT_SCORE_INSTANCE = EvidenceSupportScoreConclusion()

CONCLUSION_PROVIDERS: Mapping[str, ConclusionRecomputePort] = {
    EVIDENCE_SUPPORT_SCORE_PROVIDER_ALIAS: _EVIDENCE_SUPPORT_SCORE_INSTANCE,
    EVIDENCE_SUPPORT_SCORE_PROVIDER: _EVIDENCE_SUPPORT_SCORE_INSTANCE,
    "evidence-score": _EVIDENCE_SUPPORT_SCORE_INSTANCE,
}


def get_conclusion_provider(name: str) -> ConclusionRecomputePort:
    try:
        return CONCLUSION_PROVIDERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(set(CONCLUSION_PROVIDERS)))
        raise ValueError(
            f"unknown conclusion provider {name!r}; available: {available}"
        ) from exc


__all__ = [
    "CONCLUSION_PROVIDERS",
    "EVIDENCE_SUPPORT_SCORE_PROVIDER",
    "EVIDENCE_SUPPORT_SCORE_PROVIDER_ALIAS",
    "EVIDENCE_SCORE_PROVIDER",
    "EVIDENCE_SCORE_PROVIDER_ALIAS",
    "EvidenceSupportScoreConclusion",
    "get_conclusion_provider",
]
