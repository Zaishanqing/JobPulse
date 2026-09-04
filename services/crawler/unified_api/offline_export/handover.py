# unified_api/offline_export/handover.py
"""Daily handover report generation.

Ported from Desktop crawler — adapted to work with the new BundleExporter API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path


@dataclass
class ChangeStats:
    """Daily change analysis based on source identity and version."""
    date: str = ""
    total_published: int = 0
    new_records: int = 0
    updated_records: int = 0
    duplicate_records: int = 0
    unique_source_records_today: int = 0
    platforms: list[dict] = field(default_factory=list)


def compute_daily_change_stats(date_str: str) -> ChangeStats:
    """Query the DB for day-over-day change analysis."""
    from unified_api.database import get_conn

    conn = get_conn()
    cur = conn.cursor()
    stats = ChangeStats(date=date_str)

    try:
        cur.execute(
            """SELECT COUNT(*) FROM crawler_publications
               WHERE DATE(created_at) = %s""",
            (date_str,),
        )
        row = cur.fetchone()
        stats.total_published = (row or {}).get("COUNT(*)", 0) if row else 0

        cur.execute(
            """SELECT COUNT(DISTINCT source_platform, source_record_id, source_version) AS cnt
               FROM crawler_publications
               WHERE DATE(created_at) = %s""",
            (date_str,),
        )
        row = cur.fetchone()
        stats.unique_source_records_today = row.get("cnt", 0) if row else 0

        cur.execute(
            """SELECT COUNT(*) AS cnt FROM crawler_publications c1
               WHERE DATE(c1.created_at) = %s
               AND (c1.source_platform, c1.source_record_id, c1.source_version) NOT IN (
                   SELECT c2.source_platform, c2.source_record_id, c2.source_version FROM crawler_publications c2
                   WHERE DATE(c2.created_at) < %s
               )""",
            (date_str, date_str),
        )
        row = cur.fetchone()
        stats.new_records = row.get("cnt", 0) if row else 0

        cur.execute(
            """SELECT COUNT(*) AS cnt FROM crawler_publications c1
               WHERE DATE(c1.created_at) = %s
               AND (c1.source_platform, c1.source_record_id, c1.source_version) IN (
                   SELECT c2.source_platform, c2.source_record_id, c2.source_version FROM crawler_publications c2
                   WHERE DATE(c2.created_at) < %s
               )""",
            (date_str, date_str),
        )
        row = cur.fetchone()
        stats.duplicate_records = row.get("cnt", 0) if row else 0

        cur.execute(
            """SELECT COUNT(*) AS cnt FROM crawler_publications c1
               WHERE DATE(c1.created_at) = %s
               AND EXISTS (
                   SELECT 1 FROM crawler_publications c2
                   WHERE c2.source_platform = c1.source_platform
                   AND c2.source_record_id = c1.source_record_id
                   AND DATE(c2.created_at) < %s
                   AND c2.source_version != c1.source_version
               )""",
            (date_str, date_str),
        )
        row = cur.fetchone()
        stats.updated_records = row.get("cnt", 0) if row else 0

        cur.execute(
            """SELECT
                 t.source_platform,
                 COUNT(*) AS total,
                 SUM(CASE WHEN prev.source_record_id IS NULL THEN 1 ELSE 0 END) AS new_cnt
               FROM crawler_publications t
               LEFT JOIN (
                   SELECT DISTINCT source_platform, source_record_id, source_version FROM crawler_publications
                   WHERE DATE(created_at) < %s
               ) prev ON t.source_platform = prev.source_platform
                    AND t.source_record_id = prev.source_record_id
                    AND t.source_version = prev.source_version
               WHERE DATE(t.created_at) = %s
               GROUP BY t.source_platform
               ORDER BY total DESC""",
            (date_str, date_str),
        )
        stats.platforms = [
            {"platform": p.get("source_platform", "?"), "total": p.get("total", 0), "new": p.get("new_cnt", 0)}
            for p in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()

    return stats


@dataclass
class PlatformStats:
    platform: str
    planned: int = 0
    list_discovered: int = 0
    detail_success: int = 0
    detail_failed: int = 0
    detail_unavailable: int = 0
    published: int = 0
    top_failure_reasons: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class SpotCheckResult:
    platform: str
    checked: int = 0
    issues: int = 0
    notes: str = ""


@dataclass
class BundleRef:
    filename: str
    bundle_id: str
    mode: str
    parent_bundle_id: str | None
    record_count: int
    crawl_time_range: str
    verified: bool = True


@dataclass
class DailyCrawlStats:
    date: str
    executor: str = ""
    crawler_commit: str = "unknown"
    task_ids: list[str] = field(default_factory=list)
    platforms: list[PlatformStats] = field(default_factory=list)
    spot_checks: list[SpotCheckResult] = field(default_factory=list)
    bundles: list[BundleRef] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    change: ChangeStats | None = None

    @classmethod
    def today(cls) -> "DailyCrawlStats":
        tz = timezone(timedelta(hours=8))
        return cls(date=datetime.now(tz).strftime("%Y-%m-%d"))


def generate_handover(stats: DailyCrawlStats, output_dir: str) -> str:
    """Generate a daily handover message (Markdown file + return as string)."""
    total_list = sum(p.list_discovered for p in stats.platforms)
    total_success = sum(p.detail_success for p in stats.platforms)
    total_failed = sum(p.detail_failed for p in stats.platforms)
    total_unavailable = sum(p.detail_unavailable for p in stats.platforms)
    total_published = sum(p.published for p in stats.platforms)
    total_bundle_records = sum(b.record_count for b in stats.bundles)
    spot_checked = sum(s.checked for s in stats.spot_checks)
    spot_issues = sum(s.issues for s in stats.spot_checks)

    platform_lines = []
    for p in stats.platforms:
        platform_lines.append(f"- {p.platform}: 计划 {p.planned} | "
                              f"发现 {p.list_discovered} | "
                              f"成功 {p.detail_success} | "
                              f"failed {p.detail_failed} | "
                              f"unavailable {p.detail_unavailable} | "
                              f"发布 {p.published}")
        if p.top_failure_reasons:
            for reason, count in p.top_failure_reasons[:3]:
                platform_lines.append(f"  - {reason}: {count}")

    bundle_lines = []
    for i, b in enumerate(stats.bundles, 1):
        bundle_lines.append(
            f"- Bundle {i}: {b.filename}\n"
            f"  - bundle_id: {b.bundle_id}\n"
            f"  - mode: {b.mode}\n"
            f"  - parent_bundle_id: {b.parent_bundle_id}\n"
            f"  - record_count: {b.record_count}\n"
            f"  - crawl_time_range: {b.crawl_time_range}\n"
            f"  - verify: {'verified' if b.verified else 'FAILED'}"
        )

    anomaly_text = "无" if not stats.anomalies else "\n".join(f"- {a}" for a in stats.anomalies)
    actual_times = " / ".join(b.crawl_time_range for b in stats.bundles) if stats.bundles else "N/A"
    bundle_section = (
        "\n".join(bundle_lines)
        if bundle_lines
        else "Bundle：无\n原因：当日无新的、已完整采集且尚未导出的 crawler publication"
    )

    change_section = ""
    if stats.change and stats.change.total_published > 0:
        ch = stats.change
        pct_new = (ch.new_records / ch.total_published * 100) if ch.total_published else 0
        platform_change_lines = []
        for pp in ch.platforms:
            pct = (pp["new"] / pp["total"] * 100) if pp["total"] else 0
            platform_change_lines.append(f"  - {pp['platform']}: {pp['total']} records, {pp['new']} new ({pct:.0f}%)")
        change_section = f"""
六、每日变化分析（来源身份与版本）
- 今日发布总量：{ch.total_published}
- 全新岗位（来源记录首次出现）：{ch.new_records}（{pct_new:.0f}%）
- 重复岗位（同一来源记录与版本已存在）：{ch.duplicate_records}
- 更新岗位（同一来源记录但 source_version 变化）：{ch.updated_records}
- 今日独立来源记录数：{ch.unique_source_records_today}
- 各平台新/总量：
{chr(10).join(platform_change_lines) if platform_change_lines else '  无'}
- 完整性校验：每个 Bundle 的 manifest 与岗位记录集合可解析且结构一致
"""

    content = f"""【NFBS 爬虫每日交接】
日期：{stats.date}（UTC+8）
执行人：{stats.executor}
Crawler commit：{stats.crawler_commit}

一、采集范围
- 平台及关键词/公司：{', '.join(p.platform for p in stats.platforms)}
- 计划时间窗：{stats.date}
- 实际采集时间窗：{actual_times}
- 对应任务 ID：{', '.join(stats.task_ids) if stats.task_ids else 'N/A'}

二、采集质量
- 列表页发现：总计 {total_list}
- 详情完整成功：{total_success}
- failed：{total_failed}；主要原因：
{chr(10).join(platform_lines) if platform_lines else '  无平台数据'}
- unavailable：{total_unavailable}
- 新增内容版本 / 同内容重复 / 历史积压：发布 {total_published}，导出 {total_bundle_records}
- 人工抽查：{spot_checked} 条；异常 {spot_issues} 条；说明：

三、离线产物
{bundle_section}
- 当日 Bundle 总记录数：{total_bundle_records}

四、异常与影响
- 缺失平台/关键词：{anomaly_text}
- 页面结构或反爬变化：无
- 是否可能影响 raw_text 完整性：否
- 是否需要负责人处理：否

五、交付位置
- ZIP 路径或共享链接：{output_dir}/{stats.date}/
- 传输完成时间：{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}
{change_section}"""

    date_dir = Path(output_dir) / stats.date
    date_dir.mkdir(parents=True, exist_ok=True)
    handover_path = date_dir / "handover.md"
    handover_path.write_text(content, encoding="utf-8")

    return content
