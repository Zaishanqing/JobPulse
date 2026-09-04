"""牛客网JD采集：搜索讨论帖、识别JD块、提取正文。"""
import csv
import os
import re
import time
from DrissionPage import ChromiumPage
from historical_jd.shared import ensure_output_dir, load_companies, TARGET_KEYWORDS

SEARCH_URL = "https://www.nowcoder.com/search?type=post&query={query}"

JD_MARKERS = [
    "岗位职责", "任职要求", "职位描述", "工作职责", "岗位要求",
    "工作内容", "任职资格", "职位要求", "能力要求", "技术要求",
]


class NiukeScraper:
    def __init__(self, delay: float = 3.0):
        self.delay = delay
        self.page = None

    def _init_page(self):
        if self.page is None:
            self.page = ChromiumPage()
        return self.page

    def search_posts(self, company: str, keyword: str) -> list[str]:
        """搜索并返回帖子URL列表（第一页）。"""
        query = f"{company} {keyword} 招聘 JD".replace(" ", "+")
        url = SEARCH_URL.format(query=query)
        page = self._init_page()
        page.get(url)
        time.sleep(self.delay)

        post_urls = []
        try:
            links = page.eles("css:a[href*='/discuss/']")
            for link in links[:10]:  # 取前10条
                href = link.attr("href")
                if href:
                    full_url = href if href.startswith("http") else f"https://www.nowcoder.com{href}"
                    post_urls.append(full_url)
        except Exception as e:
            print(f"  Search failed: {e}")
        return post_urls

    def extract_jd_from_post(self, post_url: str) -> dict | None:
        """从单个帖子提取JD内容。返回 {title, full_text, jd_text, publish_time, source_url}"""
        page = self._init_page()
        try:
            page.get(post_url)
            time.sleep(2)

            title = ""
            try:
                title_el = page.ele("css:.discuss-title, h1")
                if title_el:
                    title = title_el.text
            except Exception:
                pass

            # 获取帖子正文
            full_text = ""
            try:
                content_el = page.ele("css:.post-content, .discuss-content, .post-detail-content")
                if content_el:
                    full_text = content_el.text
            except Exception:
                pass

            if not full_text:
                return None

            # 识别JD块
            jd_text = self._extract_jd_block(full_text)
            if not jd_text or len(jd_text) < 50:
                return None

            publish_time = ""
            try:
                time_el = page.ele("css:.post-time, .publish-time")
                if time_el:
                    publish_time = time_el.text
            except Exception:
                pass

            return {
                "title": title,
                "full_text": full_text[:5000],
                "jd_text": jd_text[:3000],
                "publish_time": publish_time,
                "source_url": post_url,
            }
        except Exception as e:
            print(f"  Extract failed {post_url}: {e}")
            return None

    def _extract_jd_block(self, text: str) -> str:
        """在帖子全文中识别JD块。策略：找到第一个JD锚点词后取连续文本。"""
        for marker in JD_MARKERS:
            idx = text.find(marker)
            if idx >= 0:
                # 取从marker位置开始到文本末尾或下一个非JD段落
                block = text[idx:]
                # 截断在明显非JD内容处
                cut_patterns = ["投递方式", "投递邮箱", "简历发送", "楼主", "回复", "发布于"]
                end_idx = len(block)
                for cp in cut_patterns:
                    cp_idx = block.find(cp, len(marker))
                    if 100 < cp_idx < end_idx:
                        end_idx = cp_idx
                return block[:end_idx].strip()
        return ""

    def close(self):
        if self.page:
            self.page.quit()
            self.page = None


def run_niuke_scraper(output_csv: str = None) -> str:
    """对所有公司×关键词组合搜索牛客网，提取JD并写入CSV。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l2_niuke_results.csv")

    companies = load_companies()
    scraper = NiukeScraper()
    all_results = []
    fieldnames = ["title", "company", "keyword", "full_text", "jd_text",
                  "publish_time", "source_url"]

    # 取前20家公司 × 前5个关键词作为范围（避免无限制运行）
    target_companies = [c["name"] for c in companies[:20]]
    target_kws = TARGET_KEYWORDS[:5]

    for company in target_companies:
        for kw in target_kws:
            print(f"Searching niuke: {company} + {kw}")
            post_urls = scraper.search_posts(company, kw)
            if not post_urls:
                continue
            for post_url in post_urls:
                jd = scraper.extract_jd_from_post(post_url)
                if jd:
                    jd["company"] = company
                    jd["keyword"] = kw
                    all_results.append(jd)
                time.sleep(scraper.delay)
            time.sleep(scraper.delay)

    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    scraper.close()
    print(f"Niuke done. {len(all_results)} JDs → {output_csv}")
    return output_csv


if __name__ == "__main__":
    run_niuke_scraper()
