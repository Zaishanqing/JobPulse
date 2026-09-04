from __future__ import annotations

from app.contracts.jd.extraction_model_output import (
    ModelCandidateRequirement,
    ModelEvidence,
    ModelExtractionOutput,
)
from app.infrastructure.jd_extraction_postprocessor import (
    align_evidence,
    model_output_to_v2_contract,
)


def test_align_evidence_missing_quote_returns_unresolved_without_span() -> None:
    evidence = align_evidence("jd-model", "要求掌握 Python 开发", "missing quote")

    assert evidence.alignment == "unresolved"
    assert evidence.start is None
    assert evidence.end is None
    assert evidence.occurrence_index is None


def test_unresolved_evidence_atom_is_dropped_from_v2_contract() -> None:
    output = ModelExtractionOutput(
        document_id="jd-model",
        requirements=[
            ModelCandidateRequirement(
                kind="skill",
                modality="required",
                evidence=ModelEvidence(
                    source_id="jd-model",
                    quote="missing quote",
                ),
                payload={
                    "items": [
                        {"name": "Python", "item_type": "programming_language"}
                    ]
                },
            )
        ],
    )

    contract = model_output_to_v2_contract(output, "要求掌握 Python 开发")

    assert contract.requirements == []
