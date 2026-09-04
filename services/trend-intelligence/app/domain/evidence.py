from __future__ import annotations

from uuid import uuid4
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.market import StoredSnapshot, record_quality_weight


TRACKING_QUERY_KEYS = {"from", "ref", "source", "spm"}


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/+", "/", parts.path or "/").rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
        )
    )
    return urlunsplit(((parts.scheme or "https").casefold(), host + port, path, query, ""))


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def quality_weight(snapshot: StoredSnapshot) -> tuple[float, tuple[str, ...]]:
    record = snapshot.record
    metadata = record.metadata
    flags = [str(item) for item in metadata.get("quality_flags", [])]
    precision = str(metadata.get("date_precision") or (
        "estimated" if "estimated_publish_date" in flags else "exact"
    ))
    completeness = str(metadata.get("content_completeness") or (
        "missing" if not record.content.strip() else
        "title_only" if _normalized_text(record.content) == _normalized_text(record.title) else
        "full"
    ))
    weight = record_quality_weight(record)
    if precision != "exact":
        flags.append("estimated_publish_date")
    if completeness == "title_only":
        flags.append("title_only")
    elif completeness != "full":
        flags.append("content_incomplete")
    return round(weight, 6), tuple(dict.fromkeys(flags))


@dataclass(frozen=True)
class EventCluster:
    id: str
    snapshots: tuple[StoredSnapshot, ...]


def cluster_snapshots(snapshots: list[StoredSnapshot]) -> list[EventCluster]:
    """Group syndication and near duplicates into deterministic independent events."""
    @dataclass
    class _Group:
        snapshots: list[StoredSnapshot]
        published_at: datetime
        url: str
        title: str
        content: str

    groups: list[_Group] = []
    url_groups: dict[str, _Group] = {}
    active_groups: list[_Group] = []
    max_distance_seconds = 14 * 86400
    for snapshot in sorted(snapshots, key=lambda item: (item.record.published_at, item.id)):
        record = snapshot.record
        url = normalize_url(record.url)
        title = _normalized_text(record.title)
        content = _normalized_text(record.content)[:4000]

        # Exact URL identity is independent of publication time and can be
        # resolved without scanning every previously created cluster.
        matched = url_groups.get(url) if url else None
        if matched is None:
            # Input is ordered, therefore a representative older than 14 days
            # can never match this or any later snapshot.  Keeping only the
            # active window preserves the original comparison semantics while
            # avoiding an ever-growing full-history scan.
            active_groups = [
                group for group in active_groups
                if (record.published_at - group.published_at).total_seconds()
                <= max_distance_seconds
            ]
            for group in active_groups:
                title_matcher = SequenceMatcher(None, title, group.title)
                title_similarity = (
                    title_matcher.ratio()
                    if title_matcher.quick_ratio() >= 0.88
                    else 0.0
                )
                if title_similarity >= 0.88:
                    matched = group
                    break
                if len(content) >= 80 and len(group.content) >= 80:
                    body_matcher = SequenceMatcher(None, content, group.content)
                    if (
                        body_matcher.quick_ratio() >= 0.85
                        and body_matcher.ratio() >= 0.85
                    ):
                        matched = group
                        break
        if matched is None:
            matched = _Group(
                snapshots=[snapshot],
                published_at=record.published_at,
                url=url,
                title=title,
                content=content,
            )
            groups.append(matched)
            active_groups.append(matched)
        else:
            matched.snapshots.append(snapshot)
        if url:
            url_groups.setdefault(url, matched)
    result = []
    for group in groups:
        result.append(EventCluster(str(uuid4()), tuple(group.snapshots)))
    return result
