"""搜索引擎缓存检查：Google/Bing/百度 — 作为Wayback未覆盖URL的补充。"""
import csv
import os
import time
import httpx
from bs4 import BeautifulSoup
from historical_jd.shared import ensure_output_dir

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CLIENT = httpx.Client(timeout=15, headers=HEADERS, follow_redirects=True)


def check_google_cache(url: str) -> str | None:
    """通过 Google cache: 端点获取缓存文本。"""
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    try:
        resp = CLIENT.get(cache_url)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception:
        pass
    return None


def check_baidu_cache(url: str) -> str | None:
    """通过百度快照获取缓存文本。"""
    cache_url = f"http://cache.baidu.com/{url}"
    try:
        resp = CLIENT.get(cache_url)
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text
    except Exception:
        pass
    return None


def check_bing_cache(url: str) -> str | None:
    """Bing 缓存端点。"""
    cache_url = f"https://cc.bingj.com/cache.aspx?q={url}"
    try:
        resp = CLIENT.get(cache_url)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception:
        pass
    return None


def extract_text(html: str) -> str:
    """从HTML提取可见文本，简单清洗。"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def check_all_caches(wayback_results_csv: str, output_csv: str = None) -> str:
    """对Wayback无结果的URL尝试搜索引擎缓存，写入结果CSV。"""
    if output_csv is None:
        output_csv = os.path.join(ensure_output_dir(), "l1_cache_results.csv")

    # 读取Wayback结果，找出无快照的URL
    has_snapshot = set()
    all_urls = {}
    try:
        with open(wayback_results_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row["source_url"]
                has_snapshot.add(url)
                all_urls[url] = row
    except FileNotFoundError:
        print(f"File not found: {wayback_results_csv}")
        return output_csv

    # 重新读url_list，找不在has_snapshot中的URL
    url_list_csv = os.path.join(ensure_output_dir(), "url_list_2024_2025.csv")
    no_snapshot_urls = set()
    try:
        with open(url_list_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row["url"]
                if url not in has_snapshot:
                    no_snapshot_urls.add(url)
    except FileNotFoundError:
        # fallback: 直接用wayback_results里缺失的
        pass

    fieldnames = ["source_url", "cache_source", "html", "text_preview"]
    results = []

    for url in list(no_snapshot_urls)[:500]:  # 限制500以免太慢
        print(f"Checking caches for: {url}")
        for name, checker in [("google", check_google_cache), ("baidu", check_baidu_cache), ("bing", check_bing_cache)]:
            html = checker(url)
            if html:
                text = extract_text(html)
                results.append({
                    "source_url": url,
                    "cache_source": name,
                    "html": html,
                    "text_preview": text[:2000],
                })
                print(f"  HIT: {name}")
                break  # 一个源命中就停止
        else:
            print(f"  MISS: all caches")
        time.sleep(1.5)

    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Cache check done. {len(results)} hits → {output_csv}")
    CLIENT.close()
    return output_csv


if __name__ == "__main__":
    import sys
    wayback_csv = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ensure_output_dir(), "l1_wayback_results.csv"
    )
    check_all_caches(wayback_csv)
