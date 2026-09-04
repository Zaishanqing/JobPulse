"""微信公众号JD采集 — 通过搜狗微信搜索。"""
import csv
import os
import re
import time
from DrissionPage import ChromiumPage
from historical_jd.shared import ensure_output_dir, load_companies, TARGET_KEYWORDS

SOGOU_WEIXIN = "https://weixin.sogou.com/weixin?type=2&query={query}"


class WechatScraper:
    def __init__(self, delay: float = 8.0):
        self.delay = delay
        self.page = None

    def _init_page(self) -> ChromiumPage:
        if self.page is None:
            self.page = ChromiumPage()
        return self.page

    def search_articles(self, company: str, keyword: str) -> list[dict]:
        """搜索公众号文章，返回 [{title, snippet, url, account_name, publish_time}]"""
        query = f"{company} 招聘 {keyword} 2024".replace(" ", "+")
        url = SOGOU_WEIXIN.format(query=query)
        page = self._init_page()
        page.get(url)
        time.sleep(self.delay)

        results = []
        try:
            items = page.eles("css:.news-list li, .txt-box")
            for item in items[:10]:
                try:
                    title_el = item.ele("css:h3 a, .tit a")
                    title = title_el.text if title_el else ""
                    href = title_el.attr("href") if title_el else ""

                    snippet_el = item.ele("css:.txt-info, .s-p")
                    snippet = snippet_el.text if snippet_el else ""

                    account_el = item.ele("css:.account, .s-p")
                    account = account_el.text if account_el else ""

                    time_el = item.ele("css:.s2, .time")
                    publish_time = time_el.text if time_el else ""

                    if title:
                        results.append({
                            "title": title,
                            "snippet": snippet,
                            "url": href,
                            "account_name": account,
                            "publish_time": publish_time,
                        })
                except Exception:
                    continue
        except Exception as e:
            print(f"  Sogou search failed: {e}")
        return results

    def fetch_article_content(self, article_url: str) -> str:
        """获取公众号文章全文。"""
        if not article_url:
            return ""
        page = self._init_page()
        try:
            page.get(article_url)
            time.sleep(3)
            content_el = page.ele("css:#js_content, .rich_media_content")
            if content_el:
                return content_el.text
        except Exception as e:
            print(f"  Fetch article failed: {e}")
        return ""

    def close(self):
        if self.page:
            self.page.quit()
            self.page = None


def run_wechat_scraper(output_csv: str = None, fetch_fulltext: bool = False) -> str:
    """搜索微信公众号JD文章。fetch_fulltext=True时获取全文（慢）。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l2_wechat_results.csv")

    scraper = WechatScraper()
    all_results = []
    fieldnames = ["title", "company", "keyword", "snippet", "full_text",
                  "source_url", "account_name", "publish_time"]

    target_companies = [c["name"] for c in load_companies()[:20]]
    target_kws = TARGET_KEYWORDS[:5]

    for company in target_companies:
        for kw in target_kws:
            print(f"Searching wechat: {company} + {kw}")
            articles = scraper.search_articles(company, kw)
            for art in articles:
                full_text = ""
                if fetch_fulltext:
                    full_text = scraper.fetch_article_content(art["url"])
                    time.sleep(3)
                all_results.append({
                    "title": art["title"],
                    "company": company,
                    "keyword": kw,
                    "snippet": art["snippet"][:2000],
                    "full_text": full_text[:5000],
                    "source_url": art["url"],
                    "account_name": art["account_name"],
                    "publish_time": art["publish_time"],
                })
            time.sleep(scraper.delay)

    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    scraper.close()
    print(f"WeChat done. {len(all_results)} articles → {output_csv}")
    return output_csv


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fulltext", action="store_true")
    args = parser.parse_args()
    run_wechat_scraper(fetch_fulltext=args.fulltext)
