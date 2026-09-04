"""Task 02 tests: envelope adapter, raw normalisation, deprecated method isolation."""

from __future__ import annotations

import warnings

import pytest


# ===========================================================================
# 1. Normalizer.normalize_raw() — task 02 production path
# ===========================================================================


class TestNormalizeRaw:
    def test_produces_raw_fields(self):
        from multi_company_scraper.normalizer import Normalizer

        job = Normalizer.normalize_raw(
            {
                "job_title": "Python 工程师",
                "salary_desc": "20K-30K",
                "experience": "3-5年",
                "education": "本科",
                "jd_text": " 负责后端开发\n\n要求 Python 经验 \n",
                "benefits": "五险一金",
                "source_url": "https://example.com/job/1",
            },
            company_name="测试公司",
            platform="test",
        )
        assert job.company_name == "测试公司"
        assert job.source_platform == "test"
        assert job.jd_text != ""  # cleaned but not split
        assert job.raw_text_status == "completed"
        assert job.text_canonicalization_version == "v1"
        # experience_raw / education_raw unchanged
        assert job.experience_raw == "3-5年"
        assert job.education_raw == "本科"
        # benefits stored as benefits_raw
        assert job.benefits_raw == "五险一金"

    def test_never_fills_semantic_fields(self):
        from multi_company_scraper.normalizer import Normalizer

        job = Normalizer.normalize_raw(
            {
                "job_title": "Go 工程师",
                "salary_desc": "30K-50K·16薪",
                "experience": "5-10年",
                "education": "硕士",
                "jd_text": "负责系统架构设计",
            },
            company_name="某司",
            platform="test",
        )
        assert job.salary_min == 0
        assert job.salary_max == 0
        assert job.jd_responsibility == ""
        assert job.jd_requirement == ""
        assert job.skill_tags == ""
        assert job.experience == ""
        assert job.education == ""

    def test_empty_jd_text_sets_failed_status(self):
        from multi_company_scraper.normalizer import Normalizer

        job = Normalizer.normalize_raw(
            {"job_title": "x", "jd_text": ""},
            company_name="c",
            platform="p",
        )
        assert job.raw_text_status == "failed"
        assert job.raw_text_error == "jd_text is empty"

    def test_whitespace_normalised_but_not_semantically_split(self):
        from multi_company_scraper.normalizer import Normalizer

        job = Normalizer.normalize_raw(
            {
                "job_title": "x",
                "jd_text": "岗位职责：写代码\r\n\r\n\r\n  任职要求：会Python  ",
            },
            company_name="c",
            platform="p",
        )
        # jd_text should still contain both sections — not split
        assert "岗位职责" in job.jd_text
        assert "任职要求" in job.jd_text
        assert job.jd_responsibility == ""
        assert job.jd_requirement == ""

    def test_normalize_stable_for_same_text(self):
        from multi_company_scraper.normalizer import Normalizer

        j1 = Normalizer.normalize_raw(
            {"job_title": "x", "jd_text": "hello world"},
            company_name="c",
            platform="p",
        )
        j2 = Normalizer.normalize_raw(
            {"job_title": "x", "jd_text": "hello world"},
            company_name="c",
            platform="p",
        )
        first = j1.to_dict()
        second = j2.to_dict()
        first.pop("crawl_time")
        second.pop("crawl_time")
        assert first == second

    def test_normalize_different_for_different_text(self):
        from multi_company_scraper.normalizer import Normalizer

        j1 = Normalizer.normalize_raw(
            {"job_title": "x", "jd_text": "hello"},
            company_name="c",
            platform="p",
        )
        j2 = Normalizer.normalize_raw(
            {"job_title": "x", "jd_text": "world"},
            company_name="c",
            platform="p",
        )
        assert j1.jd_text != j2.jd_text


# ===========================================================================
# 2. Deprecated methods emit warnings
# ===========================================================================


class TestDeprecationWarnings:
    def test_normalize_emits_deprecation_warning(self):
        from multi_company_scraper.normalizer import Normalizer

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Normalizer.normalize(
                {"job_title": "x", "jd_text": "text"},
                company_name="c",
                platform="p",
            )
            depwarn = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(depwarn) >= 1

    def test_split_jd_emits_deprecation_warning(self):
        from multi_company_scraper.normalizer import Normalizer

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Normalizer.split_jd("岗位职责：a\n任职要求：b")
            depwarn = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(depwarn) >= 1

    def test_normalize_salary_emits_deprecation_warning(self):
        from multi_company_scraper.normalizer import Normalizer

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Normalizer.normalize_salary("20K-30K")
            depwarn = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(depwarn) >= 1

    def test_normalize_experience_emits_deprecation_warning(self):
        from multi_company_scraper.normalizer import Normalizer

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Normalizer.normalize_experience("3-5年")
            depwarn = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(depwarn) >= 1

    def test_normalize_education_emits_deprecation_warning(self):
        from multi_company_scraper.normalizer import Normalizer

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Normalizer.normalize_education("本科")
            depwarn = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(depwarn) >= 1


# ===========================================================================
# 3. Envelope adapter
# ===========================================================================


class TestJobDataToEnvelope:
    def test_valid_job_data_converts_to_envelope(self):
        from multi_company_scraper.models.job_data import JobData

        jd_text = "Python 后端开发工程师\n负责 API 设计和实现"

        job = JobData(
            company_name="测试科技",
            job_title="Python 工程师",
            source_platform="boss_zhipin",
            job_id="rec-boss-123",
            city="北京",
            district="朝阳",
            salary_desc="20K-30K",
            jd_text=jd_text,
            source_url="https://www.zhipin.com/job/1",
            publish_date="2026-07-20",
            experience_raw="3-5年",
            education_raw="本科",
            benefits_raw="五险一金",
            text_canonicalization_version="v1",
            raw_text_status="completed",
        )

        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

        env = job_data_to_envelope(job)
        assert env.schema_version == "crawler-jd-v1"
        assert env.source_platform == "boss_zhipin"
        assert env.source_record_id == "rec-boss-123"
        assert env.job_title_raw == "Python 工程师"
        assert env.company_name_raw == "测试科技"
        assert env.region_raw == "北京 朝阳"
        assert env.raw_text == jd_text
        assert env.source_url == "https://www.zhipin.com/job/1"

    def test_empty_jd_text_raises(self):
        from multi_company_scraper.models.job_data import JobData
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

        job = JobData(
            company_name="c", job_title="t", source_platform="p",
            job_id="rec-1", jd_text="",
            raw_text_status="completed",  # triggers downstream check
            crawl_time="2026-07-20T14:30:00+00:00",
        )
        with pytest.raises(ValueError, match="jd_text is empty"):
            job_data_to_envelope(job)

    def test_same_job_converts_identically(self):
        from multi_company_scraper.models.job_data import JobData
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

        jd_text = "Go 开发工程师"
        job = JobData(
            company_name="c", job_title="t", source_platform="p",
            job_id="rec-1", jd_text=jd_text,
            raw_text_status="completed",
            crawl_time="2026-07-20T14:30:00+00:00",
        )
        e1 = job_data_to_envelope(job)
        e2 = job_data_to_envelope(job)
        assert e1.model_dump(mode="json") == e2.model_dump(mode="json")

    def test_raw_text_changes_with_text(self):
        from multi_company_scraper.models.job_data import JobData
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

        j1 = JobData(
            company_name="c", job_title="t", source_platform="p",
            job_id="rec-1", jd_text="text A",
            raw_text_status="completed",
            crawl_time="2026-07-20T14:30:00+00:00",
        )
        j2 = JobData(
            company_name="c", job_title="t", source_platform="p",
            job_id="rec-2", jd_text="text B",
            raw_text_status="completed",
            crawl_time="2026-07-20T14:30:00+00:00",
        )
        assert job_data_to_envelope(j1).raw_text != job_data_to_envelope(j2).raw_text


# ===========================================================================
# 4. compute_raw_text (deterministic processing)
# ===========================================================================


class TestComputeRawText:
    def test_normalises_whitespace(self):
        from multi_company_scraper.normalizer import compute_raw_text

        result = compute_raw_text("  hello   world  ")
        assert result == "hello world"

    def test_strips_control_characters(self):
        from multi_company_scraper.normalizer import compute_raw_text

        result = compute_raw_text("hello\x00world")
        assert result == "helloworld"

    def test_preserves_newlines(self):
        from multi_company_scraper.normalizer import compute_raw_text

        result = compute_raw_text("line1\n\n\nline2")
        assert "\n" in result
