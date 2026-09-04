#!/usr/bin/env python3
"""50家中国大厂招聘JD爬虫 - 主入口

Usage:
    python main.py --list                     # 列出所有公司
    python main.py -c 字节跳动                 # 爬取单家公司
    python main.py -p moka                    # 按平台过滤
    python main.py -o output.xlsx             # 全部爬取，指定输出文件
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

import yaml
from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.collector import JobCollector
from multi_company_scraper.excel_writer import ExcelWriter
from multi_company_scraper.scrapers.dispatcher import ScraperDispatcher

# ---------------------------------------------------------------------------
# Scraper imports — gracefully handle missing optional dependencies (e.g.
# Playwright) so the CLI still works even when some scrapers cannot load.
# ---------------------------------------------------------------------------

_scraper_import_errors: list[str] = []

try:
    from multi_company_scraper.scrapers.moka_scraper import MokaScraper  # noqa: F811
except ImportError as e:
    _scraper_import_errors.append(f"MokaScraper: {e}")

try:
    from multi_company_scraper.scrapers.feishu_scraper import FeishuScraper
except ImportError as e:
    _scraper_import_errors.append(f"FeishuScraper: {e}")

try:
    from multi_company_scraper.scrapers.baidu_scraper import BaiduScraper
except ImportError as e:
    _scraper_import_errors.append(f"BaiduScraper: {e}")

try:
    from multi_company_scraper.scrapers.tencent_scraper import TencentScraper
except ImportError as e:
    _scraper_import_errors.append(f"TencentScraper: {e}")

try:
    from multi_company_scraper.scrapers.netease_scraper import NeteaseScraper
except ImportError as e:
    _scraper_import_errors.append(f"NeteaseScraper: {e}")

try:
    from multi_company_scraper.scrapers.zhiye_scraper import ZhiyeScraper
except ImportError as e:
    _scraper_import_errors.append(f"ZhiyeScraper: {e}")

try:
    from multi_company_scraper.scrapers.playwright_scraper import PlaywrightScraper
except ImportError as e:
    _scraper_import_errors.append(f"PlaywrightScraper: {e}")

try:
    from multi_company_scraper.scrapers.liepin_scraper import LiepinScraper
except ImportError as e:
    _scraper_import_errors.append(f"LiepinScraper: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_companies(yaml_path: str) -> list[CompanyConfig]:
    """Load company configurations from a YAML file.

    Expects the file to contain a top-level ``companies`` key whose value is
    a list of dicts, each matching the :class:`CompanyConfig` schema.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [CompanyConfig.from_dict(item) for item in data["companies"]]


def setup_dispatcher() -> ScraperDispatcher:
    """Create a :class:`ScraperDispatcher` and register every available scraper.

    Scrapers whose import failed (recorded in ``_scraper_import_errors``)
    are silently skipped so the CLI remains usable without optional
    dependencies such as Playwright.
    """
    dispatcher = ScraperDispatcher()

    # Map scraper class name to the actual class reference.  If the import
    # failed the name will not be in scope — we guard with a try/NameError.
    scraper_classes: list[type] = []

    for name in [
        "MokaScraper",
        "FeishuScraper",
        "BaiduScraper",
        "TencentScraper",
        "NeteaseScraper",
        "ZhiyeScraper",
        "PlaywrightScraper",
        "LiepinScraper",
    ]:
        try:
            cls = globals()[name]
        except KeyError:
            continue
        if cls is not None:
            scraper_classes.append(cls)

    for cls in scraper_classes:
        try:
            dispatcher.register(cls())
        except Exception as e:
            logger.warning(f"Failed to instantiate scraper {cls.__name__}: {e}")

    return dispatcher


def _configure_logger(log_file: str | None = None):
    """Remove default loguru handler and add a file sink with rotation."""
    logger.remove()  # Remove default stderr handler

    # Pretty console output
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )

    # File sink with rotation
    log_path = log_file or f"crawl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger.add(
        log_path,
        rotation="10 MB",
        retention="7 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    )
    logger.info(f"Log file: {log_path}")

    # Warn about any scrapers that failed to import
    for err in _scraper_import_errors:
        logger.warning(f"Scraper unavailable: {err}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="50家中国大厂招聘JD爬虫 - 支持Moka/飞书/百度/腾讯/网易/智联/Playwright等多平台",
    )
    parser.add_argument(
        "--company", "-c",
        default="all",
        help="公司名称（如'字节跳动'）或 'all' 爬取全部 (default: all)",
    )
    parser.add_argument(
        "--output", "-o",
        default=f"招聘JD数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        help="输出Excel文件路径",
    )
    parser.add_argument(
        "--platform", "-p",
        choices=["moka", "feishu", "baidu", "tencent", "netease", "zhiye", "playwright", "liepin"],
        help="只爬取指定平台类型的公司",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="列出所有配置的公司而不爬取",
    )
    args = parser.parse_args()

    # Logging
    _configure_logger()

    # Load configuration
    config_dir = Path(__file__).parent / "config"
    companies_yaml = config_dir / "companies.yaml"
    if not companies_yaml.exists():
        logger.error(f"Config file not found: {companies_yaml}")
        sys.exit(1)

    companies = load_companies(str(companies_yaml))

    # --list mode: display companies and exit
    if args.list:
        print(f"\n{'='*72}")
        print(f"  Configured Companies ({len(companies)} total)")
        print(f"{'='*72}")
        print(f"  {'Status':6s} {'Company':16s} {'Platform':14s}  URL")
        print(f"  {'-'*68}")
        for c in companies:
            status = "[ENABLED]" if c.enabled else "[OFF   ]"
            print(f"  {status:8s} {c.name:16s} {c.platform:14s}  {c.base_url}")
        print(f"{'='*72}\n")
        return

    # Filter by company name
    if args.company != "all":
        companies = [c for c in companies if c.name == args.company]
        if not companies:
            logger.error(f"Company not found: {args.company}")
            sys.exit(1)

    # Filter by platform
    if args.platform:
        companies = [c for c in companies if c.platform == args.platform]
        if not companies:
            logger.warning(f"No companies match platform filter: {args.platform}")
            sys.exit(0)

    logger.info(f"Starting crawl for {len(companies)} company(ies)")

    # Initialise dispatcher, collector
    dispatcher = setup_dispatcher()
    collector = JobCollector()

    # Crawl each company sequentially
    for i, company in enumerate(companies, 1):
        logger.info(f"[{i}/{len(companies)}] {company.name} ({company.platform})")
        jobs = dispatcher.scrape_company(company)
        # Liepin aggregates jobs across many companies/regions — no hard cap here;
        # the scraper's own MAX_JOBS_TOTAL handles the limit internally.
        cap = 10000 if company.platform == "liepin" else 200
        if len(jobs) > cap:
            logger.info(f"  Limiting {company.name} from {len(jobs)} to {cap} jobs")
            jobs = jobs[:cap]
        collector.add_batch(jobs)
        logger.info(f"  -> {len(jobs)} jobs collected (running total: {collector.total()})")

    # Output
    stats = collector.stats()
    logger.info(f"Crawl complete. Total jobs: {stats['total_jobs']}")
    logger.info(f"Companies with data: {len(stats['companies'])}")

    if collector.total() > 0:
        is_liepin = args.platform == "liepin" or (
            args.platform is None and args.company != "all"
            and any(c.platform == "liepin" for c in companies)
        )
        if is_liepin:
            ExcelWriter.write_text_only(collector, args.output)
        else:
            ExcelWriter.write(collector, args.output)
        logger.info(f"Output saved to: {args.output}")
    else:
        logger.warning("No jobs collected — skipping Excel output")


if __name__ == "__main__":
    main()
