"""格式标准化：将 L1/L2/L3 各层输出合并并对齐字段。"""
import csv
import os
from historical_jd.shared import ensure_output_dir

TECH_DIRECTION_TAGS = {
    "ai": ["人工智能", "AI", "机器学习", "深度学习", "大模型", "NLP", "CV", "算法工程师", "自然语言", "计算机视觉", "强化学习"],
    "bigdata": ["大数据", "数据仓库", "数据分析", "数据挖掘", "数据工程", "ETL", "Hadoop", "Spark", "Flink"],
    "iot": ["物联网", "IoT", "边缘计算", "嵌入式", "传感器"],
    "intelligent_systems": ["智能系统", "推荐系统", "知识图谱", "智能决策", "搜索", "广告"],
}


def classify_tech_direction(text: str) -> list[str]:
    """根据JD文本自动打技术方向标签。"""
    tags = []
    text_lower = text.lower()
    for tag, keywords in TECH_DIRECTION_TAGS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                tags.append(tag)
                break
    return tags if tags else ["other"]


def normalize_record(source_row: dict, source_label: str) -> dict:
    """将不同来源的记录标准化为统一schema。"""
    return {
        "jd_text": source_row.get("jd_text", source_row.get("text_preview", source_row.get("full_text", ""))),
        "source_url": source_row.get("source_url", ""),
        "source_platform": source_label,
        "company": source_row.get("company", ""),
        "title": source_row.get("title", source_row.get("job_title", "")),
        "snapshot_date": source_row.get("snapshot_timestamp", source_row.get("publish_time", "")),
        "tech_tags": "",
    }


def normalize_and_dedup(input_csvs: list[str], output_csv: str = None) -> str:
    """合并多个输入 CSV 并标准化；不基于文本相似度自动删除记录。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "historical_jd_master.csv")

    all_records = []
    source_labels = {
        "wayback": "wayback",
        "cache": "search_cache",
        "niuke": "niuke",
        "maimai": "maimai",
        "wechat": "wechat",
        "paid": "paid",
    }

    for csv_path in input_csvs:
        if not csv_path or not os.path.exists(csv_path):
            continue
        # 从文件名推断来源
        fname = os.path.basename(csv_path).lower()
        label = "unknown"
        for key, val in source_labels.items():
            if key in fname:
                label = val
                break

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rec = normalize_record(row, label)
                if rec["jd_text"] and len(rec["jd_text"]) > 30:
                    all_records.append(rec)

    print(f"Total raw records: {len(all_records)}")

    # 打技术方向标签
    for rec in all_records:
        tags = classify_tech_direction(rec["jd_text"])
        rec["tech_tags"] = ";".join(tags)

    fieldnames = ["jd_text", "source_url", "source_platform", "company",
                  "title", "snapshot_date", "tech_tags"]
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_records)

    print(f"Master file → {output_csv}")
    return output_csv


def build_master_file(wayback_csv: str = None, cache_csv: str = None,
                      niuke_csv: str = None, maimai_csv: str = None,
                      wechat_csv: str = None, paid_csv: str = None,
                      output_csv: str = None) -> str:
    """便捷入口：合并所有层输出。"""
    out = ensure_output_dir()
    inputs = [
        wayback_csv or os.path.join(out, "l1_wayback_results.csv"),
        cache_csv or os.path.join(out, "l1_cache_results.csv"),
        niuke_csv or os.path.join(out, "l2_niuke_results.csv"),
        maimai_csv or os.path.join(out, "l2_maimai_results.csv"),
        wechat_csv or os.path.join(out, "l2_wechat_results.csv"),
        paid_csv or "",
    ]
    # 只保留存在的文件
    inputs = [p for p in inputs if p and os.path.exists(p)]
    return normalize_and_dedup(inputs, output_csv)


def generate_gap_report(master_csv: str, output_xlsx: str = None) -> str:
    """生成覆盖率缺口报告（公司x岗位方向矩阵）。"""
    try:
        import openpyxl
    except ImportError:
        print("openpyxl not installed, skip gap report")
        return ""

    if output_xlsx is None:
        output_xlsx = os.path.join(ensure_output_dir(), "coverage_gap_report.xlsx")

    from historical_jd.shared import load_companies
    companies = [c["name"] for c in load_companies()]

    # 读取master文件统计
    coverage = {}  # {company: {tag: count}}
    try:
        with open(master_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comp = row.get("company", "Unknown")
                tags = row.get("tech_tags", "other").split(";")
                if comp not in coverage:
                    coverage[comp] = {}
                for t in tags:
                    coverage[comp][t] = coverage[comp].get(t, 0) + 1
    except FileNotFoundError:
        pass

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Coverage Gap"
    tags_list = list(TECH_DIRECTION_TAGS.keys())
    ws.append(["公司"] + tags_list + ["总计"])

    for comp in companies:
        row = [comp]
        total = 0
        for tag in tags_list:
            cnt = coverage.get(comp, {}).get(tag, 0)
            row.append(cnt)
            total += cnt
        row.append(total)
        ws.append(row)

    wb.save(output_xlsx)
    print(f"Gap report → {output_xlsx}")
    return output_xlsx


if __name__ == "__main__":
    build_master_file()
