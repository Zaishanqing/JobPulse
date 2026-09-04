"""采集层内部原始记录。

Task 02 重构后，JobData 定位为 Crawler 内部原始数据载体。
以下字段已停止在生产路径中生成（标记为 deprecated）：

    salary_min, salary_max — 薪资数值（改由 Extraction 计算）
    jd_responsibility     — 岗位职责拆分（改由 Extraction 完成）
    jd_requirement        — 任职要求拆分（改由 Extraction 完成）
    skill_tags            — 技能标签（网站原始标签保留在 raw_payload）
    experience            — 标准化经验要求（改用 experience_raw）
    education             — 标准化学历要求（改用 education_raw）

这些字段保留是为了历史测试和旧 API 兼容，新代码不得依赖它们。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class JobData:
    """一次爬取采集到的原始岗位数据。"""

    # --- 来源身份 -------------------------------------------------------
    company_name: str
    job_title: str
    source_platform: str

    # --- 网站直接返回字段 -----------------------------------------------
    job_id: str = ""
    department: str = ""
    city: str = ""
    district: str = ""
    job_type: str = ""
    salary_desc: str = ""
    benefits_raw: str = ""  # new: 原始福利文本
    publish_date: str = ""
    source_url: str = ""
    jd_text: str = ""

    # --- 新增 raw 字段 (task 02) ---------------------------------------
    skills_raw: str = ""              # 网站原始技能/标签文本
    experience_raw: str = ""         # 网站原始经验要求文本
    education_raw: str = ""          # 网站原始学历要求文本
    raw_payload: dict[str, Any] = field(default_factory=dict)   # 原始 API 响应
    raw_html: str = ""               # 原始 HTML（如有）
    crawl_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    text_canonicalization_version: str = "v1"
    source_version: str = "1"
    raw_text_status: str = ""        # completed | failed | unavailable
    raw_text_error: str = ""

    # --- deprecated: 以下字段停止在生产路径生成 (task 02) ----------------
    salary_min: int = 0
    salary_max: int = 0
    jd_responsibility: str = ""
    jd_requirement: str = ""
    skill_tags: str = ""
    experience: str = ""
    education: str = ""

    # --- 重命名兼容 -----------------------------------------------------
    @property
    def benefits(self) -> str:
        """Deprecated: use benefits_raw instead."""
        return self.benefits_raw

    @benefits.setter
    def benefits(self, value: str) -> None:
        self.benefits_raw = value

    def to_dict(self) -> dict:
        return asdict(self)
