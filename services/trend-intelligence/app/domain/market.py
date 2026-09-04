from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from app.domain.credibility import quality_flags as credibility_quality_flags


@dataclass(frozen=True)
class SourceRecord:
    source: str
    external_id: str
    source_version: str
    title: str
    content: str
    url: str
    published_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)
    captured_at: datetime | None = None

@dataclass(frozen=True)
class StoredSnapshot:
    id: str
    record: SourceRecord


def record_quality_weight(record: SourceRecord) -> float:
    flags = [str(item) for item in record.metadata.get("quality_flags", [])]
    precision = str(record.metadata.get("date_precision") or (
        "estimated" if "estimated_publish_date" in flags else "exact"
    ))
    completeness = str(record.metadata.get("content_completeness") or (
        "missing" if not record.content.strip()
        else "title_only" if record.content.strip() == record.title.strip()
        else "full"
    ))
    date_weight = 1.0 if precision == "exact" else 0.75
    content_weight = 1.0 if completeness == "full" else 0.6 if completeness == "title_only" else 0.5
    return round(date_weight * content_weight, 6)


@dataclass(frozen=True)
class ExtractedTerm:
    snapshot_id: str
    term: str
    score: float
    week_start: str
    extractor_version: str


@dataclass(frozen=True)
class SignalObservation:
    analysis_run_id: str
    source: str
    industry_domain: str
    week_start: str
    signal_strength: float
    raw_value: float
    keywords: tuple[str, ...]
    evidence_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class PredictionResult:
    analysis_run_id: str
    job_name: str
    industry_domain: str
    emergence_score: float
    source_scores: Mapping[str, float]
    related_keywords: tuple[str, ...]
    evidence_snapshot_ids: tuple[str, ...]
    algorithm_version: str
    formula_version: str
    window_start: datetime
    window_end: datetime
    source_coverage: float
    missing_sources: tuple[str, ...]
    quality_flags: tuple[str, ...]
    config_versions: Mapping[str, str]
    score_explanation: Mapping[str, object]


def week_start(value: datetime) -> str:
    monday = value.date().fromordinal(value.date().toordinal() - value.weekday())
    return monday.isoformat()


def weekly_term_trends(terms: Sequence[ExtractedTerm]) -> dict[str, dict[str, float | None]]:
    """Return latest-week counts and growth against up to four preceding weeks.

    语义区分（P1-03）：
    - 存在至少一个有效 prior window 时，才计算 growth；
    - 历史窗口存在但 prior 平均为 0、且本周 count >= 2 时，标记 newly_observed
      （growth 置 None，不构造固定 +100%）；
    - 完全没有任何历史窗口时，标记 insufficient_history（growth 置 None）。
    """
    by_week: dict[str, dict[str, int]] = {}
    for term in terms:
        counts = by_week.setdefault(term.week_start, {})
        counts[term.term] = counts.get(term.term, 0) + 1
    if not by_week:
        return {}
    weeks = sorted(by_week)
    latest = weeks[-1]
    previous = weeks[max(0, len(weeks) - 5) : -1]
    result: dict[str, dict[str, float | None]] = {}
    for term, count in by_week[latest].items():
        prior = [by_week[item].get(term, 0) for item in previous]
        if not prior:
            result[term] = {
                "count": float(count),
                "growth": None,
                "trend_status": "insufficient_history",
                "is_new_term": True,
            }
        elif sum(prior) == 0:
            result[term] = {
                "count": float(count),
                "growth": None,
                "trend_status": "newly_observed",
                "is_new_term": True,
            }
        else:
            average = sum(prior) / len(prior)
            result[term] = {
                "count": float(count),
                "growth": round(count / average - 1, 6),
                "trend_status": "observed",
                "is_new_term": False,
            }
    return result


def aggregate_signals(
    run_id: str,
    snapshots: Sequence[StoredSnapshot],
    terms: Sequence[ExtractedTerm],
    *,
    knowledge_base: Mapping[str, Mapping[str, object]],
) -> list[SignalObservation]:
    latest_week = max((week_start(item.record.published_at) for item in snapshots), default="")
    term_trends = weekly_term_trends(terms)
    snapshot_by_id = {item.id: item for item in snapshots}
    buckets: dict[tuple[str, str], dict[str, object]] = {}

    for snapshot in snapshots:
        record = snapshot.record
        if record.source in {"arxiv", "cvf", "acl"}:
            continue
        domains = record.metadata.get("industry_domains", [])
        if not isinstance(domains, list):
            domains = []
        signal_source = record.source
        raw = float(record.metadata.get("signal_value", 1) or 1) * record_quality_weight(record)
        for domain in domains:
            bucket = buckets.setdefault((signal_source, str(domain)), {"raw": 0.0, "ids": [], "keywords": []})
            bucket["raw"] = float(bucket["raw"]) + raw
            bucket["ids"].append(snapshot.id)
            bucket["keywords"].extend(record.metadata.get("keywords", []))

    terms_by_snapshot: dict[str, list[str]] = {}
    for term in terms:
        terms_by_snapshot.setdefault(term.snapshot_id, []).append(term.term)
    for domain, spec in knowledge_base.items():
        keywords = spec["research_keywords"]
        matched_ids: list[str] = []
        matched_terms: list[str] = []
        raw = 0.0
        for snapshot_id, values in terms_by_snapshot.items():
            snapshot = snapshot_by_id.get(snapshot_id)
            if snapshot is None or snapshot.record.source not in {"arxiv", "cvf", "acl"}:
                continue
            for value in values:
                lower = value.lower()
                if any(lower in keyword or keyword in lower for keyword in keywords):
                    trend = term_trends.get(value, {"count": 1.0, "growth": 0.0})
                    growth = trend.get("growth")
                    # P1-03：growth 为 None（insufficient_history / newly_observed）时
                    # 不构造固定增长率，只按当前 count 计信号（1.0 基准）。
                    growth_factor = 1.0 if growth is None else max(1.0 + float(growth), 0.25)
                    raw += (
                        trend["count"]
                        * growth_factor
                        * record_quality_weight(snapshot.record)
                    )
                    matched_terms.append(value)
                    matched_ids.append(snapshot_id)
        if raw:
            buckets[("academic", domain)] = {
                "raw": raw,
                "ids": matched_ids,
                "keywords": matched_terms,
            }

    maxima: dict[str, float] = {}
    for (source, _domain), value in buckets.items():
        maxima[source] = max(maxima.get(source, 0.0), float(value["raw"]))
    # P1-04：absolute support 的 log 归一化基准。单个证据 1 条 → ~0.29，
    # 达到 expected 条数 → 1.0。避免"单来源单 domain"仅凭 relative 归一化天然得 1.0。
    def _absolute_support(source: str, evidence_count: int) -> float:
        expected = float(EXPECTED_MINIMUMS.get(source, 10.0))
        return min(math.log(1.0 + evidence_count) / math.log(1.0 + expected), 1.0)

    observations = []
    for (source, domain), value in sorted(buckets.items()):
        maximum = maxima[source] or 1.0
        relative = min(float(value["raw"]) / maximum, 1.0)
        evidence_count = len(value["ids"])
        absolute = _absolute_support(source, evidence_count)
        # 相对主导度 × 绝对证据量：单来源单 domain 时 relative=1.0 但 absolute
        # 由证据量决定；证据充足时两者趋同。保留 raw_value 供审计。
        observations.append(
            SignalObservation(
                analysis_run_id=run_id,
                source=source,
                industry_domain=domain,
                week_start=latest_week,
                signal_strength=round(min(relative * absolute, 1.0), 6),
                raw_value=round(float(value["raw"]), 6),
                keywords=tuple(dict.fromkeys(str(item) for item in value["keywords"]))[:10],
                evidence_snapshot_ids=tuple(dict.fromkeys(value["ids"]))[:50],
            )
        )
    return observations


def predict_jobs(
    run_id: str,
    signals: Sequence[SignalObservation],
    *,
    weights: Mapping[str, float],
    algorithm_version: str,
    formula_version: str,
    window_start: datetime,
    window_end: datetime,
    source_coverage: float,
    missing_sources: Sequence[str],
    quality_flags: Sequence[str],
    knowledge_base: Mapping[str, Mapping[str, object]],
    thresholds: Mapping[str, object],
    config_versions: Mapping[str, str],
    evidence_published_at: Mapping[str, datetime],
    configuration_drift: bool,
) -> list[PredictionResult]:
    by_domain: dict[str, dict[str, SignalObservation]] = {}
    for signal in signals:
        by_domain.setdefault(signal.industry_domain, {})[signal.source] = signal
    configured = {name: max(float(weights.get(name, 1.0)), 0.0) for name in ("policy", "academic", "funding", "github")}
    total_weight = sum(configured.values()) or 1.0
    results: list[PredictionResult] = []
    for domain, source_map in by_domain.items():
        spec = knowledge_base.get(domain)
        if spec is None:
            continue
        scores = {source: round(source_map[source].signal_strength, 6) if source in source_map else 0.0 for source in configured}
        source_contributions = {
            source: scores[source] * configured[source] / total_weight
            for source in scores
        }
        evidence = tuple(dict.fromkeys(item for signal in source_map.values() for item in signal.evidence_snapshot_ids))
        keywords = tuple(dict.fromkeys(item for signal in source_map.values() for item in signal.keywords))
        for role in spec["jobs"]:
            weighted_sources = {
                source: round(contribution * source_coverage, 6)
                for source, contribution in source_contributions.items()
            }
            heuristic = min(sum(weighted_sources.values()), 1.0)
            latest = max((evidence_published_at[item] for item in evidence if item in evidence_published_at), default=None)
            age = (window_end - latest).total_seconds() / 86400 if latest else None
            extra_flags = credibility_quality_flags(
                evidence_count=len(evidence), source_contributions=weighted_sources,
                evidence_age_days=age, growth_rate=None,
                thresholds=thresholds,
            )
            keyword_contributions = {
                keyword: round(sum(weighted_sources.values()) / len(keywords), 6)
                for keyword in keywords
            } if keywords else {}
            explanation = {
                "score_name": "emergence_score",
                "definition": "heuristic composite index; not a probability",
                "final_score": round(heuristic, 6),
                "source_contributions": weighted_sources,
                "keyword_contributions": keyword_contributions,
                "time_change_contribution": 0.0,
                "quality_penalty": round(1.0 - source_coverage, 6),
                "formula": "sum(normalized_source_signal * configured_source_weight) * source_coverage",
                "rule_contributions": {},
                "configuration_versions": dict(config_versions),
            }
            results.append(
                PredictionResult(
                    analysis_run_id=run_id,
                    job_name=str(role["name"]),
                    industry_domain=str(role["industry"]),
                    emergence_score=round(heuristic, 6),
                    source_scores=scores,
                    related_keywords=keywords[:10],
                    evidence_snapshot_ids=evidence[:50],
                    algorithm_version=algorithm_version,
                    formula_version=formula_version,
                    window_start=window_start,
                    window_end=window_end,
                    source_coverage=round(source_coverage, 6),
                    missing_sources=tuple(missing_sources),
                    quality_flags=tuple(dict.fromkeys([*quality_flags, *extra_flags])),
                    config_versions=dict(config_versions),
                    score_explanation=explanation,
                )
            )
    return sorted(results, key=lambda item: item.emergence_score, reverse=True)


def detect_domains(text: str, domain_dictionary: Mapping[str, Sequence[str]]) -> list[str]:
    lowered = text.lower()
    return [domain for domain, terms in domain_dictionary.items() if any(str(term).lower() in lowered for term in terms)]


WEIGHTS = {
    "time_window_coverage": 0.20,
    "sample_sufficiency": 0.20,
    "data_freshness": 0.15,
    "field_completeness": 0.15,
    "parse_success_rate": 0.15,
    "temporal_distribution": 0.10,
    "source_specific_signal": 0.05,
}

EXPECTED_MINIMUMS: dict[str, float] = {
    "arxiv": 50.0, "cvf": 20.0, "acl": 20.0,
    "policy": 5.0, "funding": 5.0, "github": 10.0,
}

FLAG_THRESHOLDS: dict[str, tuple[str, float]] = {
    "time_window_coverage": ("partial_time_coverage", 50.0),
    "sample_sufficiency": ("low_sample", 50.0),
    "data_freshness": ("stale_data", 50.0),
    "field_completeness": ("incomplete_fields", 70.0),
    "parse_success_rate": ("high_parse_failure", 80.0),
    "temporal_distribution": ("clustered_distribution", 50.0),
}


def compute_source_quality_grade(
    source: str,
    dimensions: Mapping[str, float],
) -> tuple[float, str, list[str]]:
    weight_map = WEIGHTS
    score = sum(
        weight_map.get(key, 0.0) * float(value)
        for key, value in dimensions.items()
    )
    score = round(min(score, 100.0), 6)
    if score >= 75:
        grade = "good"
    elif score >= 45:
        grade = "degraded"
    else:
        grade = "poor"
    flags: list[str] = []
    for key, (flag, threshold) in FLAG_THRESHOLDS.items():
        value = float(dimensions.get(key, 100))
        if key == "sample_sufficiency":
            expected = EXPECTED_MINIMUMS.get(source, 10.0)
            if value < expected * 0.5:
                flags.append(flag)
        elif value < threshold:
            flags.append(flag)
    return score, grade, flags
