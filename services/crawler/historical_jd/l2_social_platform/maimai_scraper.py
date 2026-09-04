"""脉脉JD采集：搜索内推帖、提取JD（含OCR处理截图型JD）。"""
import csv
import json
import os
import re
import time
from DrissionPage import ChromiumPage, ChromiumOptions
from historical_jd.shared import ensure_output_dir, load_companies, TARGET_KEYWORDS

MAIMAI_SEARCH = "https://maimai.cn/search/jobs?q={query}"
COOKIE_FILE = os.path.join(ensure_output_dir(), "maimai_cookies.json")


class MaimaiScraper:
    def __init__(self, cookie_file: str = COOKIE_FILE, delay: float = 5.0):
        self.cookie_file = cookie_file
        self.delay = delay
        self.page = None

    def _init_page(self) -> ChromiumPage:
        if self.page is None:
            co = ChromiumOptions()
            co.set_argument("--disable-blink-features=AutomationControlled")
            self.page = ChromiumPage(co)
            self._load_cookies()
        return self.page

    def _load_cookies(self):
        if os.path.exists(self.cookie_file):
            with open(self.cookie_file, "r") as f:
                cookies = json.load(f)
            self.page.set.cookies(cookies)

    def search_feeds(self, company: str, keyword: str) -> list[dict]:
        """搜索脉脉动态（内推帖），返回帖子列表。"""
        query = f"{company} {keyword} 内推"
        url = MAIMAI_SEARCH.format(query=query.replace(" ", "+"))
        page = self._init_page()
        page.get(url)
        time.sleep(self.delay)

        results = []
        try:
            feed_items = page.eles("css:.feed-item, .search-result-item, article")
            for item in feed_items[:15]:
                try:
                    text = item.text
                    link_el = item.ele("css:a")
                    href = link_el.attr("href") if link_el else ""
                    results.append({"text": text, "url": href})
                except Exception:
                    continue
        except Exception as e:
            print(f"  Maimai search failed: {e}")
        return results

    def extract_jd_text(self, feed: dict) -> str | None:
        """从帖子文本中提取JD块。"""
        text = feed.get("text", "")
        markers = ["岗位职责", "任职要求", "职位描述", "岗位要求", "工作内容"]
        for m in markers:
            idx = text.find(m)
            if idx >= 0:
                return text[idx:].strip()
        # 如果没有JD标记但文本较长，可能是完整JD
        if len(text) > 200:
            return text
        return None

    def close(self):
        if self.page:
            self.page.quit()
            self.page = None


def run_maimai_scraper(output_csv: str = None) -> str:
    """采集脉脉JD。需要先手动登录并保存Cookie到COOKIE_FILE。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l2_maimai_results.csv")

    if not os.path.exists(COOKIE_FILE):
        print(f"WARNING: Cookie file not found at {COOKIE_FILE}")
        print("Please login to maimai.cn manually, then save cookies as JSON.")
        print("Creating empty output file and skipping.")
        with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "keyword", "jd_text", "source_url", "feed_text"])
            writer.writeheader()
        return output_csv

    scraper = MaimaiScraper()
    all_results = []
    fieldnames = ["company", "keyword", "jd_text", "source_url", "feed_text"]

    target_companies = [c["name"] for c in load_companies()[:15]]
    target_kws = TARGET_KEYWORDS[:3]

    for company in target_companies:
        for kw in target_kws:
            print(f"Searching maimai: {company} + {kw}")
            feeds = scraper.search_feeds(company, kw)
            for feed in feeds:
                jd = scraper.extract_jd_text(feed)
                if jd:
                    all_results.append({
                        "company": company,
                        "keyword": kw,
                        "jd_text": jd[:3000],
                        "source_url": feed.get("url", ""),
                        "feed_text": feed.get("text", "")[:2000],
                    })
            time.sleep(scraper.delay)

    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    scraper.close()
    print(f"Maimai done. {len(all_results)} JDs → {output_csv}")
    return output_csv


if __name__ == "__main__":
    run_maimai_scraper()
