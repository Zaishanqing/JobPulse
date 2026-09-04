"""Tests for repository-level shared contracts (task 01 + remediation)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc
CST = timezone(timedelta(hours=8))
SAMPLE_TIME = datetime(2026, 7, 20, 14, 30, 0, tzinfo=UTC)

SAMPLE_RAW_TEXT = "  Java 开发工程师\n负责后端系统开发\n要求 3 年经验  "


def _env(**overrides) -> "CrawlerJDEnvelopeV1":  # noqa: F821
    from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

    raw = overrides.pop("raw_text", SAMPLE_RAW_TEXT)
    kwargs = dict(
        source_record_id="rec-boss-001",
        source_platform="boss_zhipin",
        source_url="https://www.zhipin.com/job/123",
        job_title_raw="Java 开发工程师",
        company_name_raw="某科技公司",
        region_raw="北京",
        publish_time_raw="2026-07-20",
        crawl_time=SAMPLE_TIME,
        raw_text=raw,
        raw_payload={"api_skill_tags": ["Java", "Spring"]},
        raw_html="<html>...</html>",
        text_canonicalization_version="v1",
    )
    kwargs.update(overrides)
    return CrawlerJDEnvelopeV1(**kwargs)


def _bundle(**overrides) -> "ExtractedJDBundleV1":  # noqa: F821
    from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1
    from jobgraph_contracts.extraction_v2 import JDExtractionResult
    from jobgraph_contracts.normalization_v2 import JDNormalizedResult, JobClassification

    doc_id = overrides.pop("document_id", "liepin:rec-001")
    raw = overrides.pop("raw_text", "test raw text")

    extraction = overrides.pop("extraction_result", JDExtractionResult(document_id=doc_id))
    normalization = overrides.pop(
        "normalized_result",
        JDNormalizedResult(
            document_id=doc_id,
            job_classification=JobClassification(
                classification_status="catalog_gap",
                review_reason_codes=["CLASSIFICATION_NOT_RUN"],
            ),
        ),
    )

    kwargs = dict(
        source_platform="liepin",
        source_record_id="rec-001",
        cleaned_text=raw,
        extraction_result=extraction,
        normalized_result=normalization,
        extraction_provider="deepseek-v4-flash",
        model_version="2026-07-20",
        extraction_run_id="run-001",
        extraction_started_at=SAMPLE_TIME,
        extraction_finished_at=SAMPLE_TIME + timedelta(seconds=60),
    )
    kwargs.update(overrides)
    return ExtractedJDBundleV1(**kwargs)


# ===========================================================================
# 1. importability
# ===========================================================================


def test_shared_contracts_importable_without_infrastructure():
    import jobgraph_contracts  # noqa: F401
    from jobgraph_contracts.base import StrictContract  # noqa: F401


# ===========================================================================
# 2. raw_text — P0-1: preservation
# ===========================================================================


class TestRawTextPreservation:
    def test_leading_trailing_whitespace_preserved(self):
        raw = "  JD原文第一行\nJD原文第二行\n"
        env = _env(raw_text=raw)
        assert env.raw_text == raw

    def test_source_identity_is_explicit_and_independent_of_raw_text(self):
        from jobgraph_contracts.source_identity import build_source_key

        raw = "  text with spaces  "
        env = _env(raw_text=raw)
        assert env.raw_text == raw
        assert env.source_version == "1"
        key = build_source_key(env.source_platform, env.source_record_id)
        changed = _env(raw_text=raw.strip())
        assert build_source_key(changed.source_platform, changed.source_record_id) == key

    def test_only_whitespace_string_rejected(self):
        with pytest.raises(ValueError, match="raw_text must contain non-whitespace"):
            _env(raw_text="   \n  \t  ")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="raw_text must contain non-whitespace"):
            _env(raw_text="")


# ===========================================================================
# 3. explicit source version utilities
# ===========================================================================


class TestExplicitSourceVersion:
    def test_default_source_version_is_stable(self):
        assert _env().source_version == "1"
        assert _bundle().source_version == "1"

    def test_explicit_source_version_is_distinct(self):
        env = _env(source_version="2")
        assert env.source_version == "2"
        assert env.source_version != _env().source_version

    def test_source_version_survives_serialization(self):
        from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

        env = _env(source_version="3")
        recreated = CrawlerJDEnvelopeV1.model_validate(env.model_dump(mode="json"))
        assert recreated.source_version == "3"


class TestSourceVersionMatch:
    def test_matching_source_version_passes(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        env = _env(source_platform="liepin", source_record_id="rec-001")
        bundle = _bundle(source_platform="liepin", source_record_id="rec-001")
        validate_bundle_matches_envelope(env, bundle)  # no exception

    def test_mismatched_source_version_raises(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        env = _env(source_platform="liepin", source_record_id="rec-001", source_version="1")
        bundle = _bundle(
            source_platform="liepin",
            source_record_id="rec-001",
            source_version="2",
        )
        with pytest.raises(ValueError, match="source_version mismatch"):
            validate_bundle_matches_envelope(env, bundle)


class TestValidateSourceIdentity:
    def test_valid_source_fields_pass(self):
        _env()  # no exception

    def test_empty_source_platform_rejected(self):
        with pytest.raises(ValueError, match="source_platform"):
            _env(source_platform="")

    def test_empty_source_record_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _env(source_record_id="   ")


# ===========================================================================
# 4. source_record_id normalisation — P1-1
# ===========================================================================


class TestNormalizeSourceRecordId:
    def test_strips_leading_trailing_whitespace(self):
        from jobgraph_contracts.source_identity import normalize_source_record_id

        assert normalize_source_record_id(" 123 ") == "123"

    def test_preserves_internal_whitespace(self):
        from jobgraph_contracts.source_identity import normalize_source_record_id

        assert normalize_source_record_id("job 123") == "job 123"

    def test_empty_after_strip_raises(self):
        from jobgraph_contracts.source_identity import normalize_source_record_id

        with pytest.raises(ValueError, match="non-empty"):
            normalize_source_record_id("   ")

    def test_non_string_raises(self):
        from jobgraph_contracts.source_identity import normalize_source_record_id

        with pytest.raises(TypeError):
            normalize_source_record_id(123)  # type: ignore[arg-type]

    def test_already_clean_is_idempotent(self):
        from jobgraph_contracts.source_identity import normalize_source_record_id

        assert normalize_source_record_id("rec-001") == "rec-001"


# ===========================================================================
# 5. source / version keys
# ===========================================================================


class TestBuildSourceKey:
    def test_different_sources_different_keys(self):
        from jobgraph_contracts.source_identity import build_source_key

        assert build_source_key("liepin", "rec-001") != build_source_key("boss_zhipin", "rec-001")

    def test_strips_record_id_whitespace(self):
        from jobgraph_contracts.source_identity import build_source_key

        k = build_source_key("test", "  rec-001  ")
        assert k == "test:rec-001"

    def test_key_consistent_with_envelope(self):
        from jobgraph_contracts.source_identity import build_source_key

        raw = "JD content"
        env = _env(
            raw_text=raw,
            source_platform="test_src",
            source_record_id="  job-42  ",
        )
        key = build_source_key("test_src", "  job-42  ")
        assert key == "test_src:job-42"
        assert key == f"{env.source_platform}:{env.source_record_id}"

    def test_platform_rejects_whitespace_only(self):
        from jobgraph_contracts.source_identity import build_source_key

        with pytest.raises(ValueError, match="source_platform must be non-empty"):
            build_source_key("   ", "rec-001")

    def test_platform_rejects_too_long(self):
        from jobgraph_contracts.source_identity import build_source_key

        with pytest.raises(ValueError, match="source_platform is too long"):
            build_source_key("p" * 65, "rec-001")

    def test_pure_whitespace_record_id_rejected(self):
        from jobgraph_contracts.source_identity import build_source_key

        with pytest.raises(ValueError, match="non-empty"):
            build_source_key("liepin", "   ")


class TestBuildSourceVersionKey:
    def test_same_source_different_version_keys(self):
        assert _env(source_version="1").source_version != _env(source_version="2").source_version

    def test_explicit_version_survives_round_trip(self):
        from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1

        bundle = _bundle(source_version="v-42")
        recreated = ExtractedJDBundleV1.model_validate(bundle.model_dump(mode="json"))
        assert recreated.source_version == "v-42"


# ===========================================================================
# 6. timezone validation — P1-3
# ===========================================================================


class TestEnsureTimezoneAware:
    def test_utc_passes(self):
        from jobgraph_contracts.source_identity import ensure_timezone_aware

        ensure_timezone_aware(datetime(2026, 1, 1, tzinfo=UTC))  # no exception

    def test_offset_passes(self):
        from jobgraph_contracts.source_identity import ensure_timezone_aware

        ensure_timezone_aware(datetime(2026, 1, 1, tzinfo=CST))  # no exception

    def test_naive_raises(self):
        from jobgraph_contracts.source_identity import ensure_timezone_aware

        with pytest.raises(ValueError, match="timezone-aware"):
            ensure_timezone_aware(datetime(2026, 1, 1))


# ===========================================================================
# 7. CrawlerJDEnvelopeV1
# ===========================================================================


class TestCrawlerJDEnvelopeV1:
    def test_valid_envelope_created(self):
        env = _env()
        assert env.schema_version == "crawler-jd-v1"
        assert env.source_platform == "boss_zhipin"
        assert env.source_version == "1"

    def test_serialize_deserialize(self):
        from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

        env = _env()
        data = env.model_dump(mode="json")
        recreated = CrawlerJDEnvelopeV1.model_validate(data)
        assert recreated.source_record_id == env.source_record_id
        assert recreated.source_version == env.source_version

    def test_source_record_id_normalized(self):
        """P1-1: whitespace is stripped during validation."""
        env = _env(source_record_id="  rec-42  ")
        assert env.source_record_id == "rec-42"

    def test_empty_source_record_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _env(source_record_id="   ")

    def test_non_json_serializable_raw_payload_rejected(self):
        with pytest.raises(ValueError, match="raw_payload is not JSON-serializable"):
            _env(raw_payload={"data": b"binary"})

    def test_explicit_source_version_is_preserved(self):
        env = _env(source_version="2")
        assert env.source_version == "2"

    def test_no_semantic_fields(self):
        env = _env()
        data = env.model_dump()
        forbidden = {
            "responsibilities",
            "requirements",
            "required_skills",
            "bonus_skills",
            "normalized_skills",
            "salary_min",
            "salary_max",
        }
        assert not (set(data.keys()) & forbidden)

    def test_optional_fields_none_by_default(self):
        raw = "minimal JD"
        from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

        env = CrawlerJDEnvelopeV1(
            source_record_id="rec-1",
            source_platform="test",
            crawl_time=SAMPLE_TIME,
            raw_text=raw,
            raw_payload={},
            text_canonicalization_version="v1",
        )
        assert env.source_url is None
        assert env.job_title_raw is None
        assert env.raw_html is None

    # -- P1-3: timezone ------------------------------------------------------

    def test_naive_crawl_time_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _env(crawl_time=datetime(2026, 1, 1))

    def test_utc_crawl_time_accepted(self):
        env = _env(crawl_time=SAMPLE_TIME)
        assert env.crawl_time == SAMPLE_TIME


# ===========================================================================
# 8. ExtractedJDBundleV1
# ===========================================================================


class TestExtractedJDBundleV1:
    def test_valid_bundle_created(self):
        bundle = _bundle()
        assert bundle.schema_version == "extracted-jd-bundle-v1"

    def test_document_id_mismatch_rejected(self):
        from jobgraph_contracts.extraction_v2 import JDExtractionResult
        from jobgraph_contracts.normalization_v2 import JDNormalizedResult, JobClassification

        with pytest.raises(ValueError, match="document_id"):
            _bundle(
                extraction_result=JDExtractionResult(document_id="doc-A"),
                normalized_result=JDNormalizedResult(
                    document_id="doc-B",
                    job_classification=JobClassification(
                        classification_status="catalog_gap",
                        review_reason_codes=["CLASSIFICATION_NOT_RUN"],
                    ),
                ),
            )

    def test_finished_before_started_rejected(self):
        with pytest.raises(ValueError, match="earlier than extraction_started_at"):
            _bundle(
                extraction_started_at=SAMPLE_TIME,
                extraction_finished_at=SAMPLE_TIME - timedelta(seconds=1),
            )

    def test_different_timezone_comparison(self):
        """Two equivalent instants in different timezones should compare equal."""
        started = datetime(2026, 7, 20, 22, 30, 0, tzinfo=CST)  # UTC+8
        finished = datetime(2026, 7, 20, 14, 31, 0, tzinfo=UTC)
        bundle = _bundle(extraction_started_at=started, extraction_finished_at=finished)
        assert bundle.extraction_started_at == started
        assert bundle.extraction_finished_at == finished

    # -- P1-3: timezone-aware datetimes --------------------------------------

    def test_naive_started_at_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _bundle(extraction_started_at=datetime(2026, 1, 1))

    def test_naive_finished_at_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _bundle(extraction_finished_at=datetime(2026, 1, 1))

    # -- P1-2: metadata non-empty validators ---------------------------------

    def test_empty_source_platform_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _bundle(source_platform="   ")

    def test_overlong_source_platform_rejected(self):
        with pytest.raises(ValueError, match="source_platform is too long"):
            _bundle(source_platform="p" * 65)

    def test_empty_extraction_provider_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _bundle(extraction_provider="")

    def test_empty_model_version_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _bundle(model_version="   ")

    def test_empty_extraction_run_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _bundle(extraction_run_id="\t ")

    def test_empty_source_record_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _bundle(source_record_id="  ")

    def test_metadata_whitespace_normalized(self):
        bundle = _bundle(
            source_platform=" liepin ",
            extraction_provider=" deepseek ",
            model_version=" v1 ",
            extraction_run_id=" run-42 ",
        )
        assert bundle.source_platform == "liepin"
        assert bundle.extraction_provider == "deepseek"
        assert bundle.model_version == "v1"
        assert bundle.extraction_run_id == "run-42"


# ===========================================================================
# 9. review_flags strict JSON — P0-3
# ===========================================================================


class TestReviewFlagsStrictJSON:
    def test_plain_primitives_pass(self):
        _bundle(review_flags=[{"key": "value"}, {"n": 1, "b": True, "x": None}])

    def test_object_instance_rejected(self):
        with pytest.raises(ValueError, match="review_flags"):
            _bundle(review_flags=[object()])

    def test_bytes_rejected(self):
        with pytest.raises(ValueError, match="review_flags"):
            _bundle(review_flags=[b"bytes"])

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="review_flags"):
            _bundle(review_flags=[{"score": float("nan")}])

    def test_inf_rejected(self):
        with pytest.raises(ValueError, match="review_flags"):
            _bundle(review_flags=[{"score": float("inf")}])

    def test_neg_inf_rejected(self):
        with pytest.raises(ValueError, match="review_flags"):
            _bundle(review_flags=[{"score": float("-inf")}])


# ===========================================================================
# 10. validate_bundle_matches_envelope — P0-2
# ===========================================================================


class TestValidateBundleMatchesEnvelope:
    def test_match_passes(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        raw = "JD text"
        env = _env(
            raw_text=raw,
            source_platform="liepin",
            source_record_id="rec-001",
        )
        bundle = _bundle(
            source_platform="liepin",
            source_record_id="rec-001",
        )
        validate_bundle_matches_envelope(env, bundle)  # no exception

    def test_source_platform_mismatch(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        env = _env(source_platform="boss_zhipin")
        bundle = _bundle(source_platform="liepin")
        with pytest.raises(ValueError, match="source_platform mismatch"):
            validate_bundle_matches_envelope(env, bundle)

    def test_source_record_id_mismatch(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        env = _env(source_platform="liepin", source_record_id="rec-a")
        bundle = _bundle(source_platform="liepin", source_record_id="rec-b")
        with pytest.raises(ValueError, match="source_record_id mismatch"):
            validate_bundle_matches_envelope(env, bundle)

    def test_source_version_mismatch(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        raw = "JD text"
        env = _env(raw_text=raw, source_platform="liepin", source_record_id="rec-001")
        bundle = _bundle(
            source_platform="liepin",
            source_record_id="rec-001",
            source_version="2",
        )
        with pytest.raises(ValueError, match="source_version mismatch"):
            validate_bundle_matches_envelope(env, bundle)

    def test_normalized_record_ids_still_match(self):
        from jobgraph_contracts.extraction_bundle import validate_bundle_matches_envelope

        raw = "JD text"
        env = _env(
            raw_text=raw,
            source_platform="liepin",
            source_record_id="  rec-001  ",
        )
        bundle = _bundle(
            source_platform="liepin",
            source_record_id="rec-001",
        )
        validate_bundle_matches_envelope(env, bundle)  # no exception

    def test_importable_from_top_level(self):
        from jobgraph_contracts import validate_bundle_matches_envelope  # noqa: F401


# ===========================================================================
# 11. existing contracts still importable
# ===========================================================================


def test_existing_contracts_still_importable():
    from jobgraph_contracts.catalog import StandardSkillRef, StandardSkillSnapshotV1
    from jobgraph_contracts.discovery import DiscoveryJDSnapshotV2
    from jobgraph_contracts.errors import ContractErrorCode
    from jobgraph_contracts.extraction_v2 import JDExtractionResult
    from jobgraph_contracts.normalization_v2 import JDNormalizedResult

    assert DiscoveryJDSnapshotV2.__name__ == "DiscoveryJDSnapshotV2"
    assert JDExtractionResult.__name__ == "JDExtractionResult"
    assert JDNormalizedResult.__name__ == "JDNormalizedResult"

    ref = StandardSkillRef(skill_id="python", canonical_name="Python")
    assert ref.skill_id == "python"
    snap = StandardSkillSnapshotV1(skill_id="python", canonical_name="Python", category_code="TECH")
    assert snap.contract_version == "capability-skill-snapshot.v1"
    assert ContractErrorCode.UNSUPPORTED_VERSION.value == "unsupported_contract_version"


@pytest.mark.parametrize(
    "validation_lineage",
    [
        {
            "state": "absent",
            "absent_reason": "validation_not_enforced",
        },
        {
            "state": "present",
            "data_validation_task_id": "validation-task-1",
            "validation_report_id": "validation-report-1",
            "validated_bundle_snapshot_id": "validation-snapshot-1",
            "validation_policy_version": "validation-policy.v1",
            "validation_conclusion": "pass",
        },
    ],
)
def test_published_jd_v3_builder_keeps_explicit_source_version(validation_lineage):
    from jobgraph_contracts.published_jd import build_published_jd_fact_v3

    payload = {
        "schema_version": "v2",
        "source_system": "main-system",
        "source_jd_id": "jd-1",
        "source_fact_id": "fact-1",
        "source_fact_version": "2026-07-28T10:00:00+00:00",
        "review_status": "published",
        "published_at": "2026-07-28T10:00:00+00:00",
        "position_fact": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端开发工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件研发",
            "candidate_positions": [
                {"position_code": "BACKEND_ENGINEER", "score": 0.95}
            ],
            "career_level": "senior",
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.95,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": ["evidence-1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "skill_facts": [],
        "requirement_facts": [],
        "company_facts": [],
        "employment_facts": [],
        "evidence": [],
        "extraction_fact": {
            "schema_version": "v2",
            "document_id": "jd-1",
        },
        "normalized_fact": {
            "schema_version": "v2",
            "document_id": "jd-1",
            "job_classification": {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "position_code": "BACKEND_ENGINEER",
                "position_name": "后端开发工程师",
                "family_code": "SOFTWARE_ENGINEERING",
                "family_name": "软件研发",
                "candidate_positions": [
                    {"position_code": "BACKEND_ENGINEER", "score": 0.95}
                ],
                "career_level": "senior",
                "leadership_scope": None,
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": ["software_engineering"],
                "confidence": 0.95,
                "classification_status": "resolved",
                "review_reason_codes": [],
                "evidence_refs": ["evidence-1"],
                "classification_policy_version": "position-classifier.v3.0",
            },
        },
        "trace_metadata": {},
        "validation_lineage": validation_lineage,
        "skill_catalog_snapshot": {
            "source": "main-system-skill-catalog",
            "catalog_version": "skill-taxonomy-catalog.v1",
            "content_hash": "a1b2c3d4" * 8,
            "effective_at": "2026-07-28T10:00:00+00:00",
            "status": "active",
        },
        "position_catalog_snapshot": {
            "source": "main-system-position-catalog",
            "catalog_version": "position-taxonomy.v3.0.0",
            "content_hash": "a1b2c3d4" * 8,
            "effective_at": "2026-07-28T10:00:00+00:00",
            "status": "active",
        },
    }
    fact = build_published_jd_fact_v3(payload)

    assert fact.source_fact_version == "2026-07-28T10:00:00+00:00"
    assert fact.source_jd_id == "jd-1"
    assert fact.validation_lineage.state == validation_lineage["state"]
    assert fact.skill_catalog_snapshot.catalog_version == "skill-taxonomy-catalog.v1"
    assert fact.skill_catalog_snapshot.content_hash == "a1b2c3d4" * 8
    assert fact.position_catalog_snapshot.catalog_version == "position-taxonomy.v3.0.0"
    assert fact.position_catalog_snapshot.content_hash == "a1b2c3d4" * 8
    with pytest.raises(ValueError, match="unexpected fields"):
        build_published_jd_fact_v3({**payload, "unexpected": True})


def test_skill_relation_snapshot_v2_represents_no_domain_without_fallback():
    from jobgraph_contracts import SkillRelationSnapshotV2

    snapshot = SkillRelationSnapshotV2(
        contract_version="skill-relation-snapshot.v2",
        position_id="POS_BACKEND",
        graph_version_id=1,
        watermark_version="watermark-config.v1",
        graph_version="graph-v1",
        authority_state="authoritative",
        generated_at=SAMPLE_TIME,
        relations=[
            {
                "skill_id": "LANG_PYTHON",
                "canonical_name": "Python",
                "classifications": [
                    {
                        "facet": "concept_class",
                        "code": "technology",
                        "is_primary": True,
                    },
                    {
                        "facet": "technology_kind",
                        "code": "language",
                        "is_primary": True,
                    },
                ],
                "taxonomy_version": "skill-taxonomy-snapshot.v1",
                "primary_modality": "required",
                "weight": 0.8,
                "confidence": 0.9,
                "importance_level": "core",
                "evidence_refs": [],
            }
        ],
    )

    relation = snapshot.model_dump(mode="json")["relations"][0]
    assert all(item["facet"] != "domain" for item in relation["classifications"])
    assert "category_code" not in relation


def _valid_position_v3_classification() -> dict[str, object]:
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端开发工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件研发",
        "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.92}],
        "career_level": "senior",
        "leadership_scope": "none",
        "technology_focus_codes": ["CLOUD_NATIVE"],
        "industry_context_codes": ["FINANCE"],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.92,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["evidence-1"],
        "classification_policy_version": "position-classifier.v3.0",
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("career_level", "INVALID_LEVEL"),
        ("leadership_scope", "INVALID_SCOPE"),
        ("technology_focus_codes", ["INVALID_TECH"]),
        ("industry_context_codes", ["INVALID_INDUSTRY"]),
        ("candidate_positions", []),
        ("evidence_refs", []),
    ],
)
def test_position_v3_rejects_invalid_enums_and_incomplete_resolved_state(field, invalid_value):
    from app.contracts.jd.normalization_v2 import JobClassification

    payload = _valid_position_v3_classification()
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        JobClassification.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("taxonomy_version", "position-taxonomy.v3.9.9"),
        ("career_level", "INVALID_LEVEL"),
        ("technology_focus_codes", ["INVALID_TECH"]),
        ("industry_context_codes", ["INVALID_INDUSTRY"]),
        ("observed_skill_domain_codes", ["INVALID_DOMAIN"]),
        ("confidence", 0.2),
        ("candidate_positions", []),
        ("evidence_refs", []),
    ],
)
def test_shared_position_v3_contract_matches_main_invariants(field, invalid_value):
    from jobgraph_contracts.normalization_v2 import JobClassification

    payload = _valid_position_v3_classification()
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        JobClassification.model_validate(payload)
