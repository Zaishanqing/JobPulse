"""L3 付费补充：占位模块。需要手动操作脉脉会员/看准网VIP后导出。"""
import csv
import os
from historical_jd.shared import ensure_output_dir


def create_empty_paid_output(output_csv: str = None) -> str:
    """创建空的L3输出文件，等待手动数据填充。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l3_paid_results.csv")
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "jd_text", "source_url",
                                                "source_platform", "snapshot_date", "notes"])
        writer.writeheader()
    print(f"L3 template -> {output_csv}")
    print("Fill this file manually with paid platform data, then re-run dedup.")
    return output_csv


def merge_paid_data(paid_csv: str = None, master_csv: str = None) -> str:
    """将手动填充的L3数据合并到master中（追加后重新去重）。"""
    from historical_jd.dedup_normalizer import normalize_and_dedup
    paid_path = paid_csv or os.path.join(ensure_output_dir(), "l3_paid_results.csv")
    master_path = master_csv or os.path.join(ensure_output_dir(), "historical_jd_master.csv")
    return normalize_and_dedup([master_path, paid_path], master_path)


if __name__ == "__main__":
    create_empty_paid_output()
