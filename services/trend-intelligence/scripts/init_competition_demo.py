"""Deterministic offline replay demo; this is never a production acquisition path."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.acquisition.application.crawl_service import CrawlService  # noqa: E402
from app.acquisition.infrastructure.connectors import (  # noqa: E402
    ConnectorRegistry,
    FixedSnapshotConnector,
)
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore  # noqa: E402
from app.api.schemas import CreateAnalysisRunRequest  # noqa: E402
from app.application.credibility import CredibilityService  # noqa: E402
from app.application.evaluation import EvaluationDatasetService  # noqa: E402
from app.application.market_prediction import MarketPrediction  # noqa: E402
from app.application.service import AnalysisRunService  # noqa: E402
from app.application.worker import AnalysisWorker  # noqa: E402
from app.domain.market import SourceRecord  # noqa: E402
from app.infrastructure.credibility_store import SqlAlchemyCredibilityStore  # noqa: E402
from app.infrastructure.database import create_database  # noqa: E402
from app.infrastructure.evaluation_store import SqlAlchemyEvaluationDatasetStore  # noqa: E402
from app.infrastructure.keyword_extractor import YakeKeywordExtractor  # noqa: E402
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore  # noqa: E402
from app.infrastructure.models import EvaluationDatasetModel  # noqa: E402
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository  # noqa: E402


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class FixedSnapshotSource:
    name: str
    records: tuple[SourceRecord, ...]

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]:
        return [item for item in self.records if window_start <= item.published_at < window_end]


def load_fixture(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_records(fixture: dict[str, object]) -> list[FixedSnapshotSource]:
    values = []
    for source, records in fixture["sources"].items():
        values.append(FixedSnapshotSource(source, tuple(
            SourceRecord(
                source=source,
                external_id=item["external_id"],
                source_version=fixture["snapshot_version"],
                title=item["title"],
                content=item["content"],
                url=item["url"],
                published_at=parse_time(item["published_at"]),
                captured_at=parse_time(item["captured_at"]),
                metadata=item["metadata"],
            )
            for item in records
        )))
    return values


def acquisition_records(
    records: list[dict[str, object]], snapshot_version: str
) -> list[dict[str, object]]:
    return [{
        "external_id": item["external_id"],
        "content_type": "json",
        "captured_at": item["captured_at"],
        "raw_content": {
            "title": item["title"], "content": item["content"], "url": item["url"],
            "published_at": item["published_at"], "metadata": item["metadata"],
        },
        "metadata": {"snapshot_version": snapshot_version, "source_reference_url": item["url"]},
    } for item in records]


def initialize_acquisition(store: SqlAlchemyAcquisitionStore, fixture: dict[str, object]) -> list[dict[str, object]]:
    chain = []
    fixed_connector = FixedSnapshotConnector()
    for source_name, records in fixture["sources"].items():
        existing = next((item for item in store.list_sources() if item["name"] == f"demo-{source_name}"), None)
        value = {
            "name": f"demo-{source_name}", "source_type": source_name,
            "endpoint_config": {"records": acquisition_records(records, fixture["snapshot_version"])},
            "rate_limit_rps": 1000, "compliance_policy": {"mode": "fixed_snapshot"},
        }
        source = store.update_source(str(existing["id"]), value) if existing else store.create_source(value)
        job = store.create_crawl_job({
            "source_id": source["id"],
            "window_start": fixture["observation_windows"][0]["start"],
            "window_end": fixture["observation_windows"][-1]["end"],
            "max_retries": 1,
        })
        demo_registry = ConnectorRegistry({source_name: fixed_connector})
        completed = CrawlService(store, registry=demo_registry).execute_job(str(job["id"]))
        if completed["status"] != "succeeded":
            raise RuntimeError(f"acquisition failed for {source_name}: {completed['error_message']}")
        observations = store.list_snapshot_observations(str(job["id"]))
        bundle = store.get_bundle_for_job(str(job["id"]))
        if bundle is None:
            raise RuntimeError(f"acquisition bundle missing for job {job['id']}")
        chain.append({
            "source": source_name, "source_id": source["id"], "job_id": job["id"],
            "snapshot_count": len(observations), "bundle_id": bundle["id"],
        })
    return chain


def run_analysis(database, fixture: dict[str, object], adapters: list[FixedSnapshotSource]) -> list[dict[str, object]]:
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    data_store = SqlAlchemyAnalysisDataStore(database.sessions)
    configs = SqlAlchemyCredibilityStore(database.sessions)
    configs.ensure_seeded()
    service = AnalysisRunService(repository, data_store=data_store, credibility_store=configs)
    pipeline = MarketPrediction(data_store, adapters, YakeKeywordExtractor(), configs)
    worker = AnalysisWorker(
        repository, worker_id="trend-competition-demo", lease_seconds=60,
        retry_delay_seconds=0, heartbeat_seconds=10, executor=pipeline.execute,
    )
    results = []
    for window in fixture["observation_windows"]:
        request = CreateAnalysisRunRequest.model_validate({
            "request_id": f"competition-demo-{fixture['snapshot_version']}-{window['key']}",
            "idempotency_key": f"competition-demo-{window['key']}",
            "time_window": {"start": window["start"], "end": window["end"]},
            "data_sources": list(fixture["sources"]),
            "weights": {"policy": 1, "academic": 1, "github": 1, "funding": 1},
            "algorithm_version": fixture["algorithm_version"],
            "formula_version": fixture["formula_version"],
            "graph_version": fixture["kg_version"],
            "config_version": fixture["config_version"],
        })
        run = service.create(request.to_command())
        if run.status.value == "pending":
            worker.run_once()
        final = service.get(run.id)
        if final is None or final.status.value != "succeeded":
            raise RuntimeError(f"analysis run {run.id} did not succeed")
        source_report = service.source_report(run.id) or {}
        results.append({
            "window": window, "run": final.to_dict(),
            "source_report": source_report,
            "signals": service.signals(run.id), "predictions": service.predictions(run.id),
        })
    return results


def ensure_evaluation_dataset(database, fixture: dict[str, object]) -> tuple[str, EvaluationDatasetService]:
    store = SqlAlchemyEvaluationDatasetStore(database.sessions)
    service = EvaluationDatasetService(store)
    with database.sessions() as session:
        existing = session.scalar(select(EvaluationDatasetModel).where(
            EvaluationDatasetModel.name == "trend-competition-history",
            EvaluationDatasetModel.version == fixture["snapshot_version"],
        ))
    if existing:
        return existing.id, service
    dataset = service.create({
        "name": "trend-competition-history", "version": fixture["snapshot_version"],
        "description": "Two fixed observation windows and historical validation labels",
        "created_by": "competition-demo",
    })
    records = []
    for label in fixture["labels"]:
        window = next(item for item in fixture["observation_windows"] if item["key"] == label["slice_key"])
        records.append({
            "entity_type": "position", "entity_id": label["entity_id"],
            "entity_name": label["candidate_key"], "prediction_cutoff": parse_time(window["end"]),
            "label_window_start": parse_time(window["end"]),
            "label_window_end": parse_time(label["validation_end"]),
            "source_reference": f"fixed-validation:{label['slice_key']}",
            "source_dedup_key": f"validation:{label['slice_key']}:{label['entity_id']}",
            "evidence": [{"observed_at": label["observed_at"], "summary": label["explanation"]}],
        })
    samples = service.generate_samples(str(dataset["id"]), "historical_hiring", records, "competition-demo")
    labels_by_slice = {item["slice_key"]: item for item in fixture["labels"]}
    for sample in samples:
        slice_key = str(sample["source_reference"]).split(":", 1)[1]
        label = labels_by_slice[slice_key]
        created = service.submit_label(str(sample["id"]), {
            "label_type": "hiring_change", "direction": label["direction"],
            "observed_value": label["observed_value"],
            "evidence": [{"observed_at": label["observed_at"], "summary": label["explanation"]}],
            "confidence_level": "high", "annotator_id": "competition-annotator",
        })
        service.review_label(str(created["id"]), "approve", "competition-reviewer", "fixed historical validation")
    store.publish_dataset(str(dataset["id"]), "competition-reviewer")
    return str(dataset["id"]), service


def run_backtest(database, fixture: dict[str, object], dataset_id: str) -> dict[str, object]:
    store = SqlAlchemyCredibilityStore(database.sessions)
    service = CredibilityService(store, SqlAlchemyEvaluationDatasetStore(database.sessions))
    request = {
        "request_id": f"competition-backtest-{fixture['snapshot_version']}",
        "idempotency_key": f"competition-backtest-{fixture['snapshot_version']}",
        "dataset_id": dataset_id, "dataset_version": fixture["snapshot_version"], "k": 3,
        "time_slices": [{
            "slice_key": window["key"], "observation_cutoff": window["end"],
            "validation_end": next(
                item["validation_end"] for item in fixture["labels"] if item["slice_key"] == window["key"]
            ),
            "weights": {"policy": 1, "academic": 1, "github": 1, "funding": 1},
            "weight_variants": [{"policy": 1, "academic": 0.8, "github": 1, "funding": 0.6}],
        } for window in fixture["observation_windows"]],
    }
    run = service.create_backtest(request)
    metrics = service.backtest_metrics(str(run["id"]))
    if run["status"] != "succeeded" or metrics is None:
        raise RuntimeError("backtest did not succeed")
    return {"run": run, "metrics": metrics}


def build_cases(fixture: dict[str, object], backtest: dict[str, object]) -> list[dict[str, object]]:
    slices = {item["slice_key"]: item for item in backtest["metrics"]["slices"]}
    cases = []
    for label in fixture["labels"]:
        result = slices[label["slice_key"]]
        prediction = next(
            (item for item in result["predictions"] if item["candidate_key"] == label["candidate_key"]), None
        )
        cases.append({
            "type": label["case_type"], "slice_key": label["slice_key"],
            "candidate": label["candidate_key"], "source_signals": prediction["source_contributions"] if prediction else {},
            "system_rank": next((index + 1 for index, item in enumerate(result["predictions"])
                                 if item["candidate_key"] == label["candidate_key"]), None),
            "validation_observation": {"direction": label["direction"], "observed_value": label["observed_value"]},
            "quality_flags": result["quality_flags"], "explanation": label["explanation"],
            "quality_impact": label["quality_impact"],
        })
    return cases


def json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write_report(output: Path, fixture_path: Path, fixture: dict[str, object], acquisition, analyses, backtest) -> None:
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "snapshot_version": fixture["snapshot_version"], "input_snapshot": str(fixture_path),
        "kg_version": fixture["kg_version"], "config_version": fixture["config_version"],
        "algorithm_version": fixture["algorithm_version"], "formula_version": fixture["formula_version"],
        "observation_windows": fixture["observation_windows"],
        "validation_window": fixture["validation_window"], "acquisition": acquisition,
        "analysis_runs": analyses, "backtest": backtest, "cases": build_cases(fixture, backtest),
        "demo_chain": ["Acquisition", "Fixed Snapshot", "Analysis Run", "Event Cluster", "Signal", "Prediction", "Evidence", "Backtest", "Trend Report"],
    }
    (output / "trend-final-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8"
    )
    aggregate = backtest["metrics"]["aggregate"]
    lines = [
        "# Trend Intelligence 竞赛演示报告", "",
        f"- 输入快照：`{fixture['snapshot_version']}`", f"- KG 版本：`{fixture['kg_version']}`",
        f"- 配置版本：`{fixture['config_version']}`", f"- Precision@3：`{aggregate.get('precision_at_3')}`",
        f"- 排名相关性：`{aggregate.get('ranking_correlation')}`", "",
        "## 案例", "",
    ]
    for case in report["cases"]:
        lines.extend([
            f"### {case['type']}：{case['candidate']}", "",
            f"- 系统排名：{case['system_rank']}", f"- 来源贡献：`{json.dumps(case['source_signals'], ensure_ascii=False)}`",
            f"- 验证窗口：`{json.dumps(case['validation_observation'], ensure_ascii=False)}`",
            f"- 质量标记：`{json.dumps(case['quality_flags'], ensure_ascii=False)}`",
            f"- 质量影响：{case['quality_impact']}", f"- 说明：{case['explanation']}", "",
        ])
    (output / "trend-final-report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize and run the reproducible Trend competition demo")
    parser.add_argument("--database-url", default=os.getenv("TREND_INTELLIGENCE_DATABASE_URL"))
    parser.add_argument("--fixture", type=Path, default=SERVICE_ROOT / "demo" / "fixed_history_v1.json")
    parser.add_argument("--output", type=Path, default=SERVICE_ROOT / "demo-output")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or TREND_INTELLIGENCE_DATABASE_URL is required")
    fixture = load_fixture(args.fixture.resolve())
    database = create_database(args.database_url)
    acquisition = initialize_acquisition(SqlAlchemyAcquisitionStore(database.sessions), fixture)
    analyses = run_analysis(database, fixture, source_records(fixture))
    dataset_id, _ = ensure_evaluation_dataset(database, fixture)
    backtest = run_backtest(database, fixture, dataset_id)
    write_report(args.output.resolve(), args.fixture.resolve(), fixture, acquisition, analyses, backtest)
    print(json.dumps({
        "status": "succeeded", "output": str(args.output.resolve()),
        "precision_at_3": backtest["metrics"]["aggregate"].get("precision_at_3"),
        "ranking_correlation": backtest["metrics"]["aggregate"].get("ranking_correlation"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
