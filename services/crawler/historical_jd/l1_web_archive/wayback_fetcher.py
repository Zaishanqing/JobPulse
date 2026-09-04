"""Wayback Machine CDX API 批量查询 + 快照HTML下载。"""
import csv
import os
import time
import httpx
from bs4 import BeautifulSoup
from historical_jd.shared import ensure_output_dir

WAYBACK_CDX = "http://web.archive.org/cdx/search/cdx"
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{timestamp}id_/{url}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HistoricalJD/1.0)"}


class WaybackFetcher:
    def __init__(self, from_date: str = "20240101", to_date: str = "20251231",
                 delay: float = 1.5):
        self.from_date = from_date
        self.to_date = to_date
        self.delay = delay
        self.client = httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True)

    def query_snapshots(self, target_url: str) -> list[dict]:
        """查询某URL在时间范围内的所有快照。返回 [{timestamp, original, statuscode, digest}]"""
        params = {
            "url": target_url,
            "from": self.from_date,
            "to": self.to_date,
            "output": "json",
            "fl": "timestamp,original,statuscode,digest",
            "limit": "100",
            "filter": "statuscode:200",
        }
        try:
            resp = self.client.get(WAYBACK_CDX, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if len(data) <= 1:  # only header row
                return []
            # data[0] is header row
            snapshots = []
            for row in data[1:]:
                snapshots.append({
                    "timestamp": row[0],
                    "original": row[1],
                    "statuscode": row[2],
                    "digest": row[3],
                })
            return snapshots
        except Exception as e:
            print(f"  CDX query failed for {target_url}: {e}")
            return []

    def dedup_by_digest(self, snapshots: list[dict]) -> list[dict]:
        """相同digest只保留最早时间戳。"""
        seen = {}
        for s in snapshots:
            d = s["digest"]
            if d not in seen or s["timestamp"] < seen[d]["timestamp"]:
                seen[d] = s
        return sorted(seen.values(), key=lambda x: x["timestamp"])

    def download_snapshot_html(self, timestamp: str, url: str) -> str:
        """下载快照HTML页面。"""
        snapshot_url = WAYBACK_SNAPSHOT.format(timestamp=timestamp, url=url)
        try:
            resp = self.client.get(snapshot_url)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"  Download failed {snapshot_url}: {e}")
            return ""

    def extract_text(self, html: str) -> str:
        """从HTML提取可见文本，简单清洗。"""
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")
        # 移除 script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def fetch_one(self, target_url: str, company: str = "",
                  platform: str = "", url_type: str = "") -> list[dict]:
        """查询并下载一个URL的全部有用快照。返回结果列表。"""
        results = []
        snapshots = self.query_snapshots(target_url)
        if not snapshots:
            return results
        deduped = self.dedup_by_digest(snapshots)
        # 每月只保留一份（取每月最早的快照）
        monthly = {}
        for s in deduped:
            month = s["timestamp"][:6]  # YYYYMM
            if month not in monthly:
                monthly[month] = s
        for s in monthly.values():
            html = self.download_snapshot_html(s["timestamp"], target_url)
            text = self.extract_text(html)
            results.append({
                "source_url": target_url,
                "company": company,
                "platform": platform,
                "url_type": url_type,
                "snapshot_timestamp": s["timestamp"],
                "archive_url": WAYBACK_SNAPSHOT.format(timestamp=s["timestamp"], url=target_url),
                "html": html,
                "text_preview": text[:2000],
            })
            time.sleep(self.delay)
        return results

    def close(self):
        self.client.close()


def fetch_wayback_snapshots(url_list_csv: str, output_csv: str = None,
                            max_urls: int = 0) -> str:
    """批量处理URL清单CSV，下载所有快照并写入结果CSV。max_urls=0表示全部。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l1_wayback_results.csv")

    fetcher = WaybackFetcher()
    all_results = []
    fieldnames = ["source_url", "company", "platform", "url_type",
                  "snapshot_timestamp", "archive_url", "html", "text_preview"]

    with open(url_list_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        urls = list(reader)

    total = len(urls)
    if max_urls > 0:
        urls = urls[:max_urls]

    for i, row in enumerate(urls):
        print(f"[{i+1}/{total}] {row['url']}")
        results = fetcher.fetch_one(
            row["url"],
            company=row.get("company", ""),
            platform=row.get("platform", ""),
            url_type=row.get("url_type", ""),
        )
        all_results.extend(results)
        print(f"  Found {len(results)} monthly snapshots")
        time.sleep(fetcher.delay)

        # 每100条增量保存
        if (i + 1) % 100 == 0:
            _write_csv(output_csv, fieldnames, all_results)

    _write_csv(output_csv, fieldnames, all_results)
    fetcher.close()
    print(f"Done. {len(all_results)} total snapshots → {output_csv}")
    return output_csv


def _write_csv(path: str, fieldnames: list, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    import sys
    url_csv = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ensure_output_dir(), "url_list_2024_2025.csv"
    )
    fetch_wayback_snapshots(url_csv)
