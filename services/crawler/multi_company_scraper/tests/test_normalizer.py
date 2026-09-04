import pytest
from multi_company_scraper.normalizer import Normalizer


class TestSalaryNormalization:
    def test_range_k(self):
        lo, hi = Normalizer.normalize_salary("30K-60K")
        assert lo == 30
        assert hi == 60

    def test_range_k_space(self):
        lo, hi = Normalizer.normalize_salary("30K - 60K")
        assert lo == 30
        assert hi == 60

    def test_single_value(self):
        lo, hi = Normalizer.normalize_salary("20K")
        assert lo == 20
        assert hi == 20

    def test_annual_salary(self):
        lo, hi = Normalizer.normalize_salary("30K-50K·16薪")
        assert lo == 40  # 30*16/12
        assert hi == 66   # 50*16/12

    def test_yuan_format(self):
        lo, hi = Normalizer.normalize_salary("15000-30000元/月")
        assert lo == 15
        assert hi == 30

    def test_mianyi(self):
        lo, hi = Normalizer.normalize_salary("薪资面议")
        assert lo == 0
        assert hi == 0

    def test_empty(self):
        lo, hi = Normalizer.normalize_salary("")
        assert lo == 0
        assert hi == 0


class TestExperienceNormalization:
    def test_one_to_three(self):
        assert Normalizer.normalize_experience("1-3年") == "1-3年"

    def test_three_to_five(self):
        assert Normalizer.normalize_experience("3-5年经验") == "3-5年"

    def test_buxian(self):
        assert Normalizer.normalize_experience("经验不限") == "不限"

    def test_yingjiesheng(self):
        assert Normalizer.normalize_experience("应届生") == "1年以下"

    def test_five_plus(self):
        assert Normalizer.normalize_experience("5-10年") == "5-10年"

    def test_ten_plus(self):
        assert Normalizer.normalize_experience("10年以上") == "10年以上"

    def test_unknown(self):
        assert Normalizer.normalize_experience("随便写") == ""


class TestEducationNormalization:
    def test_benke(self):
        assert Normalizer.normalize_education("本科及以上") == "本科"

    def test_shuoshi(self):
        assert Normalizer.normalize_education("硕士") == "硕士"

    def test_dazhuan(self):
        assert Normalizer.normalize_education("大专及以上") == "大专"

    def test_boshi(self):
        assert Normalizer.normalize_education("博士") == "博士"

    def test_buxian(self):
        assert Normalizer.normalize_education("学历不限") == "不限"

    def test_unknown(self):
        assert Normalizer.normalize_education("随便") == ""


class TestJdSplit:
    def test_split_responsibility_and_requirement(self):
        text = "岗位职责：\n1. 负责后端开发\n2. 优化系统性能\n任职要求：\n1. 熟悉Python\n2. 3年以上经验"
        resp, req = Normalizer.split_jd(text)
        assert "后端开发" in resp
        assert "优化系统性能" in resp
        assert "熟悉Python" in req
        assert "3年以上经验" in req

    def test_no_split_markers(self):
        text = "负责后端开发，熟悉Python"
        resp, req = Normalizer.split_jd(text)
        assert resp == text
        assert req == ""


class TestNormalize:
    def test_full_normalize(self):
        """Task 02: normalize() delegates to normalize_raw() — no semantic output."""
        raw = {
            "job_title": "后端工程师",
            "job_id": "123",
            "department": "技术部",
            "city": "北京",
            "district": "海淀区",
            "job_type": "社招",
            "experience": "3-5年",
            "education": "本科及以上",
            "salary_desc": "30K-60K·15薪",
            "jd_text": "岗位职责：开发\n任职要求：Python",
            "skill_tags": "Python,Go",
            "benefits": "六险一金",
            "publish_date": "2026-07-01",
            "source_url": "https://example.com/job/123",
        }
        jd = Normalizer.normalize(raw, "测试公司", "moka")
        assert jd.company_name == "测试公司"
        assert jd.job_title == "后端工程师"
        # Task 02: semantic fields are NO LONGER filled in production
        assert jd.salary_min == 0
        assert jd.salary_max == 0
        assert jd.experience == ""
        assert jd.education == ""
        assert jd.jd_responsibility == ""
        assert jd.jd_requirement == ""
        # raw fields are preserved
        assert jd.experience_raw == "3-5年"
        assert jd.education_raw == "本科及以上"
        assert jd.raw_text_status == "completed"
        assert jd.benefits_raw == "六险一金"
        assert jd.jd_text != ""  # text is cleaned but not split
