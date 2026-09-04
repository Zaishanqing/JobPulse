from dataclasses import dataclass

from app.domain.jd import Document, NormalizationResult
from app.domain.json_types import JsonObject
from app.infrastructure.jd_extraction_mapper import domain_to_extraction, extraction_to_domain
from app.infrastructure.jd_normalization_mapper import domain_to_normalization, normalization_to_domain


@dataclass(frozen=True)
class PersistenceBundle:
    schema_version: str
    normalization_schema_version: str
    extraction_payload: JsonObject
    normalization_payload: JsonObject


def to_persistence(
    document: Document,
    normalization: NormalizationResult,
    *,
    extraction_version: str = "v2",
    normalization_version: str = "v2",
) -> PersistenceBundle:
    extraction = domain_to_extraction(document, extraction_version)
    normalized = domain_to_normalization(normalization, normalization_version)
    return PersistenceBundle(
        schema_version=extraction_version,
        normalization_schema_version=normalization_version,
        extraction_payload=extraction.model_dump(mode="json"),
        normalization_payload=normalized.model_dump(mode="json"),
    )


def from_persistence(
    extraction_payload: JsonObject,
    normalization_payload: JsonObject,
    *,
    schema_version: str | None = None,
    normalization_schema_version: str | None = None,
) -> tuple[Document, NormalizationResult]:
    document = extraction_to_domain(extraction_payload, schema_version)
    normalization = normalization_to_domain(
        normalization_payload, normalization_schema_version
    )
    if document.document_id != normalization.document_id:
        raise ValueError("Extraction and normalization document IDs do not match")
    return document, normalization
