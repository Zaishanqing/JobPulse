from pydantic import BaseModel, Field
from typing import Optional


class BossCrawlRequest(BaseModel):
    keyword: str
    city: str
    pages: int = 5


class BossJobItem(BaseModel):
    id: int
    job_title: str
    job_salary: str
    job_company: str
    company_city: str
    keyword: str
    job_skill: Optional[str] = None
    job_lable: Optional[str] = None
    create_time: Optional[str] = None


class BossJobListResponse(BaseModel):
    jobs: list[BossJobItem]
    total: int
    page: int
    page_size: int


class BossStats(BaseModel):
    total_jobs: int
    city_distribution: list[dict]
    keyword_distribution: list[dict]


class CompanyCrawlRequest(BaseModel):
    company_name: str = "all"
    platform: Optional[str] = None


class CompanyJobItem(BaseModel):
    id: int
    company_name: str
    platform: str
    job_title: str
    salary_min: int
    salary_max: int
    experience: Optional[str] = None
    education: Optional[str] = None
    skill_tags: Optional[str] = None
    location: Optional[str] = None
    source_platform: str
    source_url: Optional[str] = None
    created_at: Optional[str] = None


class CompanyJobListResponse(BaseModel):
    jobs: list[CompanyJobItem]
    total: int
    page: int
    page_size: int


class CompanyInfo(BaseModel):
    name: str
    platform: str
    base_url: str
    enabled: bool


class CompanyStats(BaseModel):
    total_jobs: int
    company_distribution: list[dict]
    platform_distribution: list[dict]


class LiepinCrawlRequest(BaseModel):
    keywords: Optional[list[str]] = None
    cities: Optional[list[str]] = None
    pages: int = Field(default=5, ge=1, le=100)


class CrawlResponse(BaseModel):
    task_id: str
    status: str = "started"
    message: str = "爬虫任务已启动"


class KeywordItem(BaseModel):
    keyword: str


class CityItem(BaseModel):
    city: str
    city_code: str


# ---------------------------------------------------------------------------
# Envelope export (task 02)
# ---------------------------------------------------------------------------


class EnvelopeExportRequest(BaseModel):
    company_name: Optional[str] = None
    platform: Optional[str] = None
    keyword: Optional[str] = None
    city: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=100)
