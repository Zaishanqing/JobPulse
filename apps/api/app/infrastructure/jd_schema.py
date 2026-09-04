"""Application boundary for schema-versioned JD data."""

from app.infrastructure.jd_contract_mapper import to_api_dto, to_legacy_dto
from app.infrastructure.jd_persistence_mapper import (
    PersistenceBundle,
    from_persistence,
    to_persistence,
)
from app.infrastructure.jd_extraction_mapper import domain_to_extraction, extraction_to_domain
from app.infrastructure.jd_normalization_mapper import (
    domain_to_normalization,
    normalization_to_domain,
)

from app.infrastructure.jd_pipeline import (
    extract_jd,
    normalize_document,
    validate_document_publishable,
)
from app.domain.json_types import (
    JsonObject,
    JsonValue as JsonValue,
    freeze_json_object,
    thaw_json_object,
)
from app.contexts.jd_lifecycle import (
    JDLegacyFields,
    JDSchemaBundle as SchemaBundle,
    JDSchemaPersistence,
    JDSchemaView,
)
from app.contracts.jd.normalization_v2 import JobClassification


def build_schema_bundle(document_id: str, raw_text: str, fallback_title: str) -> SchemaBundle:
    extraction_contract = extract_jd(document_id, raw_text, fallback_title)
    document = extraction_to_domain(extraction_contract.model_dump(mode="json"))
    validate_document_publishable(document)
    return SchemaBundle(document=document, normalization=normalize_document(document))


def load_schema_bundle(
    extraction_payload: JsonObject,
    normalization_payload: JsonObject,
    *,
    schema_version: str | None = None,
    normalization_schema_version: str | None = None,
) -> SchemaBundle:
    document, normalization = from_persistence(
        extraction_payload,
        normalization_payload,
        schema_version=schema_version,
        normalization_schema_version=normalization_schema_version,
    )
    return SchemaBundle(document=document, normalization=normalization)


def edit_schema_bundle(
    extraction_payload: JsonObject,
    normalization_payload: JsonObject | None,
    *,
    schema_version: str | None = None,
    normalization_schema_version: str | None = None,
) -> SchemaBundle:
    document = extraction_to_domain(extraction_payload, schema_version)
    validate_document_publishable(document)
    normalization = (
        normalization_to_domain(normalization_payload, normalization_schema_version)
        if normalization_payload is not None
        else normalize_document(document)
    )
    if document.document_id != normalization.document_id:
        raise ValueError("Extraction and normalization document IDs do not match")
    return SchemaBundle(document=document, normalization=normalization)


def persist_schema_bundle(bundle: SchemaBundle) -> PersistenceBundle:
    return to_persistence(
        bundle.document,
        bundle.normalization,
        extraction_version=bundle.document.contract_version,
        normalization_version=bundle.normalization.contract_version,
    )


def schema_api_dto(bundle: SchemaBundle) -> JsonObject:
    return to_api_dto(bundle.document, bundle.normalization)


def schema_api_dto_partial(
    extraction_payload: JsonObject | None,
    normalization_payload: JsonObject | None,
    *,
    schema_version: str,
    normalization_schema_version: str,
) -> JsonObject:
    extraction_result = None
    normalized_result = None
    if extraction_payload is not None:
        document = extraction_to_domain(extraction_payload, schema_version)
        extraction_result = domain_to_extraction(document, document.contract_version).model_dump(
            mode="json"
        )
    if normalization_payload is not None:
        normalization = normalization_to_domain(normalization_payload, normalization_schema_version)
        normalized_result = domain_to_normalization(
            normalization, normalization.contract_version
        ).model_dump(mode="json")
    return {
        "schema_version": schema_version,
        "normalization_schema_version": normalization_schema_version,
        "extraction_result": extraction_result,
        "normalized_result": normalized_result,
        "extraction_status": "available" if extraction_result is not None else "missing",
        "normalization_status": ("available" if normalized_result is not None else "missing"),
    }


def schema_legacy_dto(bundle: SchemaBundle, fallback_title: str) -> JDLegacyFields:
    return to_legacy_dto(bundle.document, bundle.normalization, fallback_title=fallback_title)


def validate_schema_publishable(bundle: SchemaBundle) -> None:
    validate_document_publishable(bundle.document)
    classification = dict(bundle.normalization.job_classification or {})
    try:
        validated = JobClassification.model_validate(classification)
    except ValueError as exc:
        raise ValueError("position_classification_v3_invalid") from exc
    if validated.classification_status not in {"resolved", "manually_confirmed"}:
        raise ValueError("position_classification_not_publishable")
    if not validated.position_code:
        raise ValueError("position_code_required_for_publish")


class VersionedJDSchemaAdapter:
    def build(self, document_id: str, raw_text: str, fallback_title: str) -> SchemaBundle:
        return build_schema_bundle(document_id, raw_text, fallback_title)

    def load(
        self,
        extraction_payload: JsonObject,
        normalization_payload: JsonObject,
        *,
        schema_version: str | None = None,
        normalization_schema_version: str | None = None,
    ) -> SchemaBundle:
        return load_schema_bundle(
            thaw_json_object(freeze_json_object(extraction_payload, field="extraction_payload")),
            thaw_json_object(
                freeze_json_object(normalization_payload, field="normalization_payload")
            ),
            schema_version=schema_version,
            normalization_schema_version=normalization_schema_version,
        )

    def edit(
        self,
        extraction_payload: JsonObject,
        normalization_payload: JsonObject | None,
        *,
        schema_version: str | None = None,
        normalization_schema_version: str | None = None,
    ) -> SchemaBundle:
        return edit_schema_bundle(
            thaw_json_object(freeze_json_object(extraction_payload, field="extraction_payload")),
            thaw_json_object(
                freeze_json_object(normalization_payload, field="normalization_payload")
            )
            if normalization_payload is not None
            else None,
            schema_version=schema_version,
            normalization_schema_version=normalization_schema_version,
        )

    def persist(self, bundle: SchemaBundle) -> JDSchemaPersistence:
        stored = persist_schema_bundle(bundle)
        return JDSchemaPersistence(
            stored.extraction_payload,
            stored.normalization_payload,
            stored.schema_version,
            stored.normalization_schema_version,
        )

    def legacy(self, bundle: SchemaBundle, fallback_title: str) -> JDLegacyFields:
        return schema_legacy_dto(bundle, fallback_title)

    def validate_publishable(self, bundle: SchemaBundle) -> None:
        validate_schema_publishable(bundle)

    def view(
        self,
        extraction_payload: JsonObject | None,
        normalization_payload: JsonObject | None,
        *,
        schema_version: str,
        normalization_schema_version: str,
    ) -> JDSchemaView:
        payload = schema_api_dto_partial(
            thaw_json_object(freeze_json_object(extraction_payload, field="extraction_payload"))
            if extraction_payload is not None
            else None,
            thaw_json_object(
                freeze_json_object(normalization_payload, field="normalization_payload")
            )
            if normalization_payload is not None
            else None,
            schema_version=schema_version,
            normalization_schema_version=normalization_schema_version,
        )
        return JDSchemaView(
            payload["extraction_result"],
            payload["normalized_result"],
            payload["extraction_status"],
            payload["normalization_status"],
        )
