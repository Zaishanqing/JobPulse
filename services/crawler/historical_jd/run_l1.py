"""L1 一键执行流水线：URL构建 → Wayback抓取 → 缓存补充。"""

from historical_jd.l1_web_archive.url_list_builder import build_url_list
from historical_jd.l1_web_archive.wayback_fetcher import fetch_wayback_snapshots
from historical_jd.l1_web_archive.cache_checker import check_all_caches
from historical_jd.shared import ensure_output_dir


def run_l1_pipeline(max_urls: int = 0) -> dict:
    """执行完整L1流水线。max_urls=0表示全部。返回统计。"""
    out = ensure_output_dir()
    stats = {}

    print("=" * 60)
    print("STEP 1/3: Building URL list...")
    url_csv = build_url_list()

    print("\n" + "=" * 60)
    print("STEP 2/3: Fetching Wayback Machine snapshots...")
    wayback_csv = fetch_wayback_snapshots(url_csv, max_urls=max_urls)
    # 统计
    import csv
    wayback_count = 0
    with open(wayback_csv, "r", encoding="utf-8-sig") as f:
        wayback_count = sum(1 for _ in f) - 1  # minus header
    stats["wayback_snapshots"] = wayback_count

    print("\n" + "=" * 60)
    print("STEP 3/3: Checking search engine caches for missed URLs...")
    cache_csv = check_all_caches(wayback_csv)
    cache_count = 0
    try:
        with open(cache_csv, "r", encoding="utf-8-sig") as f:
            cache_count = sum(1 for _ in f) - 1
    except FileNotFoundError:
        pass
    stats["cache_hits"] = cache_count

    print("\n" + "=" * 60)
    print(f"L1 Pipeline Complete: {stats}")
    print(f"  Wayback output: {wayback_csv}")
    print(f"  Cache output:   {cache_csv}")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-urls", type=int, default=0, help="Limit URLs for testing")
    args = parser.parse_args()
    run_l1_pipeline(max_urls=args.max_urls)
