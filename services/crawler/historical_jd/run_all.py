"""全流水线一键执行入口。"""

from historical_jd.run_l1 import run_l1_pipeline


def run_full_pipeline(stage: str = "all", max_urls: int = 0) -> dict:
    """执行完整流水线。stage: l1, l2, l3, dedup, gap, all"""
    stats = {}

    if stage in ("l1", "all"):
        stats["l1"] = run_l1_pipeline(max_urls=max_urls)

    if stage in ("l2", "all"):
        from historical_jd.l2_social_platform.maimai_scraper import run_maimai_scraper
        from historical_jd.l2_social_platform.niuke_scraper import run_niuke_scraper
        from historical_jd.l2_social_platform.wechat_scraper import run_wechat_scraper

        print("\n" + "=" * 60)
        print("L2: Running social platform scrapers (semi-auto)...")
        stats["niuke"] = run_niuke_scraper()
        stats["maimai"] = run_maimai_scraper()
        stats["wechat"] = run_wechat_scraper()
        print("L2 complete. Review output files before proceeding to L3.")

    if stage in ("l3", "all"):
        from historical_jd.l3_paid_supplement.paid_exporter import create_empty_paid_output

        stats["l3"] = create_empty_paid_output()

    if stage in ("dedup", "all"):
        from historical_jd.dedup_normalizer import build_master_file

        print("\n" + "=" * 60)
        print("Running dedup and building master file...")
        master_csv = build_master_file()
        stats["master"] = master_csv

    if stage in ("gap", "all"):
        from historical_jd.dedup_normalizer import generate_gap_report

        master = stats.get("master", "")
        if master:
            stats["gap"] = generate_gap_report(master)

    print("\n" + "=" * 60)
    print("Pipeline Complete. Summary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="历史JD采集全流水线")
    parser.add_argument("--stage", choices=["l1", "l2", "l3", "dedup", "gap", "all"],
                        default="all")
    parser.add_argument("--max-urls", type=int, default=0,
                        help="Limit L1 URLs (for testing)")
    args = parser.parse_args()
    run_full_pipeline(stage=args.stage, max_urls=args.max_urls)
