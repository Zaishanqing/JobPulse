from __future__ import annotations

import gzip
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

from app.domain.market import SourceRecord, detect_domains

UTC = timezone.utc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_date(value: str, fallback: datetime) -> datetime:
    text = (value or "").strip().replace(".", "-")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return _utc(parsed)
    except ValueError:
        try:
            return _utc(parsedate_to_datetime(text))
        except (TypeError, ValueError):
            return _utc(fallback)


def _parse_exact_date(value: str) -> datetime | None:
    text = (value or "").strip().replace(".", "-")
    if not text:
        return None
    if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        normalized = text.replace("/", "-")
        try:
            return _utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))
        except ValueError:
            pass
    try:
        return _utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def _published_at(soup: BeautifulSoup) -> datetime | None:
    for selector, attribute in (
        ("meta[name='citation_publication_date']", "content"),
        ("meta[name='citation_date']", "content"),
        ("meta[name='dc.date']", "content"),
        ("meta[property='article:published_time']", "content"),
        ("time[datetime]", "datetime"),
        ("[data-published-at]", "data-published-at"),
        ("[data-publish-date]", "data-publish-date"),
    ):
        node = soup.select_one(selector)
        if node is not None:
            parsed = _parse_exact_date(str(node.get(attribute, "")))
            if parsed is not None:
                return parsed
    return None


CONFERENCE_FALLBACK_MONTHS = {
    "CVPR": (6, 15), "ICCV": (10, 1), "ECCV": (9, 1),
    "ACL": (7, 1), "EMNLP": (11, 1), "NAACL": (6, 1),
}


class ConfiguredSource:
    configurations: dict[str, dict]

    def configure(self, configurations: dict[str, dict]) -> None:
        self.configurations = configurations

    def domains(self, text: str) -> list[str]:
        return detect_domains(text, self.configurations["domain_dictionary"])


class ArxivSource(ConfiguredSource):
    name = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"
    categories = ("cs.AI", "cs.CL", "cs.CV", "cs.LG", "cs.RO", "cs.CR", "cs.SE", "cs.DB")
    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    }

    def __init__(
        self,
        client: httpx.Client,
        *,
        limit: int = 200,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.limit = limit
        self.before_request = before_request

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        category_query = " OR ".join(f"cat:{item}" for item in self.categories)
        query = f"({category_query}) AND submittedDate:[{window_start:%Y%m%d%H%M} TO {window_end:%Y%m%d%H%M}]"
        records: list[SourceRecord] = []
        start = 0
        batch_size = min(100, self.limit)
        total = None
        while True:
            if self.before_request is not None:
                self.before_request()
            response = self.client.get(
                self.endpoint,
                params={
                    "search_query": query, "start": start, "max_results": batch_size,
                    "sortBy": "submittedDate", "sortOrder": "descending",
                },
            )
            response.raise_for_status()
            root = ET.fromstring(response.text)
            total_element = root.find("opensearch:totalResults", self.namespace)
            if total_element is not None and total_element.text:
                try:
                    total = int(total_element.text)
                except ValueError:
                    total = None
            entries = root.findall("atom:entry", self.namespace)
            if not entries:
                break
            for entry in entries:
                identifier = entry.findtext("atom:id", default="", namespaces=self.namespace).strip().rsplit("/", 1)[-1]
                title = " ".join(entry.findtext("atom:title", default="", namespaces=self.namespace).split())
                if not identifier or not title:
                    continue
                abstract = " ".join(entry.findtext("atom:summary", default="", namespaces=self.namespace).split())
                published = _parse_date(
                    entry.findtext("atom:published", default="", namespaces=self.namespace), window_start,
                )
                categories = [item.get("term", "") for item in entry.findall("atom:category", self.namespace)]
                records.append(SourceRecord(
                    source=self.name, external_id=identifier, source_version="arxiv.atom.v1",
                    title=title, content=abstract, url=f"https://arxiv.org/abs/{identifier}",
                    published_at=published,
                    metadata={"categories": categories, "industry_domains": self.domains(f"{title} {abstract}")},
                ))
            start += len(entries)
            if len(records) >= self.limit:
                break
            if total is not None:
                if start >= total:
                    break
            elif len(entries) < batch_size:
                break
        return records[: self.limit]


def _extract_abstract(soup: BeautifulSoup) -> str | None:
    for selector, attribute in (
        ("div#abstract", None),
        ("div.abstract", None),
        ("section#abstract", None),
        ("meta[name='citation_abstract']", "content"),
        ("meta[name='description']", "content"),
        ("meta[property='og:description']", "content"),
    ):
        node = soup.select_one(selector)
        if node is not None:
            text = node.get(attribute) if attribute else node.get_text(" ", strip=True)
            cleaned = " ".join(text.split())
            if len(cleaned) >= 20:
                return cleaned
    return None


class CvfSource(ConfiguredSource):
    name = "cvf"

    def __init__(self, client: httpx.Client, *, limit: int = 500) -> None:
        self.client = client
        self.limit = limit

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        failed_details = 0
        conferences = []
        for year in range(window_start.year, window_end.year + 1):
            conferences.append(("CVPR", year))
            conferences.append(("ICCV" if year % 2 else "ECCV", year))
        for conference, year in conferences:
            response = self.client.get(f"https://openaccess.thecvf.com/{conference}{year}?day=all")
            if response.status_code == 404:
                continue
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.select("dt.ptitle a, dt a[href*='papers']"):
                href = anchor.get("href", "")
                title = " ".join(anchor.get_text(" ", strip=True).split())
                if not href or not title:
                    continue
                identifier = href.strip("/").replace("/", ":")
                url = str(httpx.URL("https://openaccess.thecvf.com").join(href))
                try:
                    detail = self.client.get(url)
                    if detail.status_code == 404:
                        continue
                    detail.raise_for_status()
                except httpx.HTTPError:
                    failed_details += 1
                    continue
                published_at = _published_at(BeautifulSoup(detail.text, "html.parser"))
                estimated = False
                if published_at is None:
                    fallback_month, fallback_day = CONFERENCE_FALLBACK_MONTHS.get(conference, (7, 1))
                    published_at = _utc(datetime(year, fallback_month, fallback_day))
                    estimated = True
                if not window_start <= published_at <= window_end:
                    continue
                abstract = _extract_abstract(BeautifulSoup(detail.text, "html.parser"))
                content = abstract if abstract else title
                metadata = {"conference": conference, "industry_domains": self.domains(title)}
                if estimated:
                    metadata["quality_flags"] = ["estimated_publish_date"]
                records.append(SourceRecord(
                    source=self.name, external_id=identifier, source_version=f"cvf.html.{year}",
                    title=title, content=content, url=url, published_at=published_at, metadata=metadata,
                ))
                if len(records) >= self.limit:
                    return records
        if failed_details:
            records = [replace(record, metadata={
                **record.metadata,
                "quality_flags": list(dict.fromkeys([
                    *record.metadata.get("quality_flags", []),
                    "partial_source_failure",
                ])),
                "failed_detail_requests": failed_details,
            }) for record in records]
        return records


class AclSource(ConfiguredSource):
    name = "acl"
    paper_pattern = re.compile(r"/(\d{4})\.(acl|emnlp|naacl)-[^/]+\.\d+/", re.I)

    def __init__(self, client: httpx.Client, *, limit: int = 500) -> None:
        self.client = client
        self.limit = limit

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        records = []
        failed_details = 0
        for year in range(window_start.year, window_end.year + 1):
            for event in ("acl", "emnlp", "naacl"):
                response = self.client.get(f"https://aclanthology.org/events/{event}-{year}/")
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.select("a[href]"):
                    href = anchor.get("href", "")
                    match = self.paper_pattern.fullmatch(href)
                    title = " ".join(anchor.get_text(" ", strip=True).split())
                    if match is None or len(title) < 8:
                        continue
                    identifier = href.strip("/")
                    url = str(httpx.URL("https://aclanthology.org").join(href))
                    try:
                        detail = self.client.get(url)
                        if detail.status_code == 404:
                            continue
                        detail.raise_for_status()
                    except httpx.HTTPError:
                        failed_details += 1
                        continue
                    published_at = _published_at(BeautifulSoup(detail.text, "html.parser"))
                    estimated = False
                    if published_at is None:
                        fallback_month, fallback_day = CONFERENCE_FALLBACK_MONTHS.get(event.upper(), (7, 1))
                        published_at = _utc(datetime(year, fallback_month, fallback_day))
                        estimated = True
                    if not window_start <= published_at <= window_end:
                        continue
                    abstract = _extract_abstract(BeautifulSoup(detail.text, "html.parser"))
                    content = abstract if abstract else title
                    metadata = {"conference": event.upper(), "industry_domains": self.domains(title)}
                    if estimated:
                        metadata["quality_flags"] = ["estimated_publish_date"]
                    records.append(SourceRecord(
                        source=self.name, external_id=identifier, source_version=f"acl-anthology.html.{year}",
                        title=title, content=content, url=url, published_at=published_at, metadata=metadata,
                    ))
                    if len(records) >= self.limit:
                        return records
        if failed_details:
            records = [replace(record, metadata={
                **record.metadata,
                "quality_flags": list(dict.fromkeys([
                    *record.metadata.get("quality_flags", []),
                    "partial_source_failure",
                ])),
                "failed_detail_requests": failed_details,
            }) for record in records]
        return records


class PolicySource(ConfiguredSource):
    name = "policy"
    endpoint = "https://sousuo.www.gov.cn/search-gov/data"

    def __init__(
        self,
        client: httpx.Client,
        *,
        per_query: int = 20,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.per_query = per_query
        self.before_request = before_request

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        records: dict[str, SourceRecord] = {}
        for query in self.configurations["policy_keywords"]["queries"]:
            if self.before_request is not None:
                self.before_request()
            response = self.client.get(self.endpoint, params={"t": "zhengcelibrary_gw_bm_gb", "p": 0, "n": self.per_query, "q": query, "searchfield": "title", "sort": "pubtime", "sortType": 1})
            response.raise_for_status()
            cat_map = ((response.json().get("searchVO") or {}).get("catMap") or {})
            for category in cat_map.values():
                for item in category.get("listVO", []):
                    url = item.get("url", "")
                    title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                    if not url or not title:
                        continue
                    published = _parse_date(item.get("pubtimeStr", ""), window_start)
                    if not window_start <= published <= window_end:
                        continue
                    content = (item.get("summary") or "").strip()
                    identifier = f"gov:{url.strip()}"
                    text = f"{title} {content}"
                    domains = self.domains(text)
                    raw_date = item.get("pubtimeStr", "")
                    records[identifier] = SourceRecord(source=self.name, external_id=identifier, source_version="gov-search.v1", title=title, content=content, url=url, published_at=published, metadata={"publisher": item.get("puborg", ""), "keywords": domains, "industry_domains": domains, "signal_value": 1, "date_precision": "exact" if _parse_exact_date(str(raw_date)) else "estimated"})
        return list(records.values())


class FundingSource(ConfiguredSource):
    name = "funding"
    funding_words = (
        "融资", "A轮", "B轮", "C轮", "D轮", "天使轮", "种子轮", "Pre-A",
        "IPO", "战略投资", "获投",
    )

    def __init__(
        self,
        client: httpx.Client,
        *,
        endpoints: list[str] | None = None,
    ) -> None:
        self.client = client
        self.endpoints = endpoints or [
            "https://www.36kr.com/feed",
            "https://www.itjuzi.com/feed",
        ]

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        seen_urls: set[str] = set()
        records: list[SourceRecord] = []
        failed_endpoints: list[str] = []
        for endpoint in self.endpoints:
            try:
                response = self.client.get(endpoint)
                response.raise_for_status()
            except Exception:
                failed_endpoints.append(endpoint)
                continue
            root = ET.fromstring(response.text)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
                if not any(word in f"{title} {description}" for word in self.funding_words):
                    continue
                raw_date = item.findtext("pubDate") or ""
                published = _parse_date(raw_date, window_start)
                if not window_start <= published <= window_end:
                    continue
                url = (item.findtext("link") or "").strip()
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                identifier = f"funding:{(url or title).strip()}"
                domains = self.domains(f"{title} {description}")
                records.append(SourceRecord(
                    source=self.name, external_id=identifier,
                    source_version="funding.rss.v2", title=title, content=description,
                    url=url, published_at=published,
                    metadata={
                        "industry_domains": domains, "keywords": domains,
                        "signal_value": 1, "feed_endpoint": endpoint,
                        "date_precision": "exact" if _parse_exact_date(raw_date) else "estimated",
                        "quality_flags": ["partial_source_failure"] if failed_endpoints else [],
                        "failed_endpoints": list(failed_endpoints),
                    },
                ))
        if failed_endpoints:
            records = [
                replace(record, metadata={
                    **record.metadata,
                    "quality_flags": list(dict.fromkeys([
                        *record.metadata.get("quality_flags", []),
                        "partial_source_failure",
                    ])),
                    "failed_endpoints": list(failed_endpoints),
                })
                for record in records
            ]
        return records


class GithubSource(ConfiguredSource):
    name = "github"
    base = "https://data.gharchive.org"
    search_endpoint = "https://api.github.com/search/repositories"

    def __init__(
        self,
        client: httpx.Client,
        *,
        hours: int = 3,
        max_hours: int = 168,
        search_mode: bool = False,
    ) -> None:
        self.client = client
        self.hours = hours
        self.max_hours = max_hours
        # search_mode=True 时用 GitHub Search API 按 created 日期范围回溯，
        # 解决 GH Archive 只能覆盖最近数小时、无法重建多周历史窗口的问题。
        self.search_mode = search_mode

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        if self.search_mode:
            return self._collect_search(window_start, window_end)
        return self._collect_archive(window_start, window_end)

    def _collect_search(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        """用 GitHub Search API 统计各 topic 在该窗口内新增的仓库数。

        每个 topic 发一次 `topic:<topic> created:<start>..<end>` 查询，
        用 total_count 作为该 topic 窗口内的信号强度（新增仓库数）。
        """
        topic_domains = self.configurations["github_topics"]
        start = _utc(window_start)
        end = _utc(window_end)
        records: list[SourceRecord] = []
        for topic, domain in topic_domains.items():
            query = f"topic:{topic} created:{start:%Y-%m-%d}..{end:%Y-%m-%d}"
            response = self.client.get(
                self.search_endpoint,
                params={"q": query, "per_page": 1},
            )
            if response.status_code == 403:
                # 触发 rate limit：保留失败信号，不中断其它 topic。
                records.append(SourceRecord(
                    source=self.name,
                    external_id=f"github-search:{start:%Y-%m-%d}:{topic}",
                    source_version="github.search.v1",
                    title=f"GitHub topic: {topic}",
                    content="rate_limited",
                    url=self.search_endpoint,
                    published_at=end,
                    metadata={
                        "industry_domains": [domain], "keywords": [topic],
                        "signal_value": 0, "rate_limited": True,
                        "quality_flags": ["github_rate_limited"],
                    },
                ))
                continue
            response.raise_for_status()
            try:
                payload = response.json()
                total = int(payload.get("total_count", 0))
            except (ValueError, json.JSONDecodeError):
                total = 0
            records.append(SourceRecord(
                source=self.name,
                external_id=f"github-search:{start:%Y-%m-%d}:{topic}",
                source_version="github.search.v1",
                title=f"GitHub topic: {topic}",
                content=f"new_repos={total}",
                url=self.search_endpoint,
                published_at=end,
                metadata={
                    "industry_domains": [domain], "keywords": [topic],
                    "signal_value": total,
                    "new_repositories": total,
                    "query": query,
                },
            ))
        return records

    def _collect_archive(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        topic_domains = self.configurations["github_topics"]
        window_hours = int((window_end - window_start).total_seconds() / 3600)
        hours_to_fetch = min(window_hours, self.max_hours)
        partial = window_hours > self.max_hours
        current = _utc(window_end).replace(minute=0, second=0, microsecond=0)
        stats: dict[tuple[str, str], dict[str, object]] = defaultdict(
            lambda: {"stars": 0, "forks": 0, "repos": set()}
        )
        hours = []
        for offset in range(hours_to_fetch):
            hour = current.fromtimestamp(current.timestamp() - offset * 3600, tz=UTC)
            hours.append(hour)

        def fetch_hour(hour: datetime):
            url = f"{self.base}/{hour:%Y-%m-%d}-{hour.hour}.json.gz"
            response = self.client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content

        failed_hours = 0
        with ThreadPoolExecutor(max_workers=min(8, len(hours)), thread_name_prefix="github-archive") as pool:
            futures = {pool.submit(fetch_hour, hour): hour for hour in hours}
            for future in as_completed(futures):
                try:
                    content = future.result()
                except httpx.HTTPError:
                    failed_hours += 1
                    continue
                if content is None:
                    continue
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as archive:
                    lines = archive.read().decode("utf-8", errors="ignore").splitlines()
                for line in lines:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") not in {"WatchEvent", "ForkEvent"}:
                        continue
                    repo = ((event.get("repo") or {}).get("name") or "").lower()
                    topic = next((key for key in topic_domains if key in repo.replace("_", "-")), None)
                    if topic is None:
                        continue
                    domain = topic_domains[topic]
                    bucket = stats[(topic, domain)]
                    bucket["stars" if event["type"] == "WatchEvent" else "forks"] += 1
                    bucket["repos"].add(repo)
        records = []
        for (topic, domain), values in stats.items():
            signal = int(values["stars"]) + int(values["forks"])
            metadata: dict[str, object] = {
                "industry_domains": [domain], "keywords": [topic],
                "stars": values["stars"], "forks": values["forks"],
                "active_repositories": len(values["repos"]), "signal_value": signal,
            }
            if partial:
                metadata["quality_flags"] = ["github_partial_window"]
            if failed_hours:
                metadata["quality_flags"] = list(dict.fromkeys([
                    *metadata.get("quality_flags", []), "partial_source_failure",
                ]))
                metadata["failed_hour_requests"] = failed_hours
            records.append(SourceRecord(
                source=self.name, external_id=f"{window_end:%Y-%m-%d}:{topic}",
                source_version="gharchive.events.v1",
                title=f"GitHub topic: {topic}",
                content=f"stars={values['stars']} forks={values['forks']} repos={len(values['repos'])}",
                url=self.base, published_at=_utc(window_end), metadata=metadata,
            ))
        return records
