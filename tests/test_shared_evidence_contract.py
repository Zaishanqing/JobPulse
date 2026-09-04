from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.extraction_v2 import Evidence as JDEvidence


def test_unresolved_evidence_may_omit_span() -> None:
    evidence = Evidence(source_id="src-1", quote="Python")
    assert evidence.start is None
    assert evidence.end is None
    assert evidence.alignment == "unresolved"


@pytest.mark.parametrize(
    "payload",
    [
        {"start": 0},
        {"end": 5},
        {"start": -1, "end": 5},
        {"start": 5, "end": 2},
        {"start": 3, "end": 3},
    ],
)
def test_invalid_evidence_spans_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Evidence(
            source_id="src-1",
            quote="Python",
            **payload,
        )


def test_exact_evidence_requires_span() -> None:
    with pytest.raises(ValidationError, match="exact evidence requires"):
        Evidence(source_id="src-1", quote="Python", alignment="exact")


def test_jd_evidence_is_the_shared_contract() -> None:
    assert JDEvidence is Evidence


def test_shared_evidence_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Evidence(source_id="src-1", quote="Python", unexpected=True)


def test_source_id_is_stripped_but_quote_preserves_exact_text() -> None:
    evidence = Evidence(
        source_id="  src-1  ",
        quote=" Python ",
        start=0,
        end=8,
        alignment="exact",
    )
    assert evidence.source_id == "src-1"
    assert evidence.quote == " Python "
    assert evidence.is_exact_for(" Python ") is True


def test_whitespace_only_quote_is_rejected() -> None:
    with pytest.raises(ValidationError, match="quote must not be empty"):
        Evidence(source_id="src-1", quote="   ")


def test_assignment_validation_is_restored() -> None:
    evidence = Evidence(source_id="src-1", quote="Python")

    with pytest.raises(ValidationError, match="quote must not be empty"):
        evidence.quote = "   "
    with pytest.raises(ValidationError):
        evidence.start = -1
