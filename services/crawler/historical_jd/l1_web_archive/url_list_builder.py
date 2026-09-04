"""从现有数据库 + companies.yaml 构建待查 URL 清单。"""
import csv
import os
from historical_jd.shared import (
    get_db_connection, load_companies, TARGET_KEYWORDS, ensure_output_dir
)

BOSS_KEYWORD_URL = "https://www.zhipin.com/web/geek/job?query={kw}&city=100010000"
LIEPIN_KEYWORD_URL = "https://www.liepin.com/zhaopin/?key={kw}&city=010"


def generate_candidate_urls() -> list[dict]:
    """生成候选URL列表，返回 [{"company": str, "platform": str, "url": str, "url_type": str}]"""
    urls = []

    # 尝试从DB提取已有的URL（DB可能不可用）
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. 从 bosszp 表提取已有URL
        cursor.execute("SELECT DISTINCT source_url FROM bosszp WHERE source_url IS NOT NULL AND source_url != ''")
        for (source_url,) in cursor.fetchall():
            urls.append({"company": "", "platform": "bosszp", "url": source_url, "url_type": "known_detail"})

        # 2. 从 multi_company_jobs 表提取已有URL
        cursor.execute("SELECT DISTINCT company_name, source_platform, source_url FROM multi_company_jobs WHERE source_url IS NOT NULL AND source_url != ''")
        for company, platform, source_url in cursor.fetchall():
            urls.append({"company": company, "platform": platform, "url": source_url, "url_type": "known_detail"})

        conn.close()
    except Exception as e:
        print(f"[DB] 数据库不可用，跳过已有URL提取: {e}")

    # 3. 为每家enabled公司生成官网招聘搜索页URL
    companies = load_companies()
    for c in companies:
        base = c.get("base_url", "")
        if not base:
            continue
        urls.append({
            "company": c["name"],
            "platform": c.get("platform", "unknown"),
            "url": base,
            "url_type": "careers_home"
        })
        # 为每个关键词生成搜索URL变体
        for kw in TARGET_KEYWORDS:
            urls.append({
                "company": c["name"],
                "platform": c.get("platform", "unknown"),
                "url": f"{base.rstrip('/')}?keyword={kw}",
                "url_type": "keyword_search"
            })

    # 4. Boss直聘 + 猎聘搜索页 URL（按关键词）
    boss_city_codes = ["101010100", "101020100", "101280100", "101280600", "101210100"]
    for kw in TARGET_KEYWORDS:
        for cc in boss_city_codes:
            urls.append({"company": "", "platform": "bosszp", "url": f"https://www.zhipin.com/web/geek/job?query={kw}&city={cc}", "url_type": "search_page"})
        urls.append({"company": "", "platform": "liepin", "url": LIEPIN_KEYWORD_URL.format(kw=kw), "url_type": "search_page"})

    # 去重
    seen = set()
    deduped = []
    for u in urls:
        if u["url"] not in seen:
            seen.add(u["url"])
            deduped.append(u)
    return deduped


def build_url_list(output_csv: str = None) -> str:
    """构建URL清单并写入CSV，返回输出路径。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "url_list_2024_2025.csv")
    urls = generate_candidate_urls()
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "platform", "url", "url_type"])
        writer.writeheader()
        writer.writerows(urls)
    print(f"Generated {len(urls)} candidate URLs -> {output_csv}")
    return output_csv


if __name__ == "__main__":
    build_url_list()
