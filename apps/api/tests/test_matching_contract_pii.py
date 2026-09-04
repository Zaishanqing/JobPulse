from __future__ import annotations

import pytest

from app.infrastructure.matching_contracts import _safe_text


@pytest.mark.parametrize(
    "text",
    [
        "2017.09 - 2021.06",
        "2021.07 - 2022.12  某软件服务公司 | 软件工程师",
        "2024.03 - 2025.02  多格式简历解析平台 | 核心开发",
        "2024-03-01 至 2025-02-28",
        "教育时间 2017.09 - 2021.06，主修计算机科学",
        # 技能/产品关键词不是联系方式
        "技能要求:全栈侧重后端 Java 后端开发经验 Telegram AI Agent prompt hermes AI数字员工",
        "负责微信小程序开发与 QQ 浏览器多端适配",
        "熟悉 WeChat 生态与 Telegram Bot 开发",
        "解决多门店并发调度的性能与稳定性问题(500-7000+门店规模)",
    ],
)
def test_safe_text_accepts_date_ranges(text: str) -> None:
    assert _safe_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "+86 138-1234-5678",
        "13812345678",
        "138-1234-5678",
        "candidate@example.com",
        "微信 13812345678",
        "telegram @jobgraph_user",
        "linkedin.com/in/jobgraph-user",
    ],
)
def test_safe_text_preserves_contact_text(text: str) -> None:
    assert _safe_text(text) == text
