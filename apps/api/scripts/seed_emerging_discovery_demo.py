"""Seed a recent, repeatable dataset for the emerging-position discovery demo.

The script deliberately uses the normal discovery application service after
creating reviewed JD snapshots.  This keeps the demo data on the same path as
real data and also populates the remote candidate lifecycle service.

Run from ``apps/api`` inside the main-backend container, for example::

    python scripts/seed_emerging_discovery_demo.py

The script is idempotent: the twenty fixed JD ids and the fixed request id are
reused on subsequent runs.  These versioned, synthetic algorithm fixtures use
the audited demo period (2026-07-27 to 2026-08-08) and occupy five consecutive
observation windows so the formal lifecycle can accumulate repeated
observations.  Their ``source_type`` is always ``curated_recent_sample``; they
must not be presented as externally collected market facts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contexts.discovery import (  # noqa: E402
    RunDiscoveryCommand,
    build_rolling_discovery_requests,
)
from app.contexts.discovery.application_types import ClusterProjection  # noqa: E402
from app.contexts.emerging_positions import (  # noqa: E402
    EmergingActor,
    ReviewEmergingDefinitionCommand,
)
from app.bootstrap.container import _build_runtime  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.domain.values import freeze  # noqa: E402
from app.infrastructure.discovery import (  # noqa: E402
    SqlAlchemyDiscoveryRepository,
    discovery_run_payload,
    discovery_run_result,
)
from app.integrations.emerging_discovery.client import EmergingDiscoveryClient  # noqa: E402
from app.models.jd import JobDescription  # noqa: E402
from app.models.jd_parse_result import JDParseResult  # noqa: E402
from app.models.jd_publication import JDPublication  # noqa: E402


_DEMO_TITLES = ("Agentic RAG 应用工程师",) * 20
_DEMO_COMPANIES = (
    "企业知识管理服务商",
    "智能客服解决方案商",
    "流程自动化平台",
    "AI 应用研发企业",
)
_DEMO_DATES = tuple(
    published
    for published in (
        date(2026, 7, 27),
        date(2026, 7, 30),
        date(2026, 8, 2),
        date(2026, 8, 5),
        date(2026, 8, 8),
    )
    for _ in range(4)
)
DEMO_JDS = tuple(
    {
        "id": f"demo-recent-agent-v6-jd-{index:03d}",
        "title": title,
        "publish_date": published,
        "source_name": f"近期招聘样本·{index}",
        "company_name": _DEMO_COMPANIES[(index - 1) % len(_DEMO_COMPANIES)],
        "responsibilities": [
            "设计连接企业知识库与业务工具的 Agentic RAG 工作流",
            "建立智能体效果评测与运行监控机制，推动应用稳定上线并持续优化",
        ],
        "required_skills": [
            {"raw_skill": "Python"},
            {"raw_skill": "RAG"},
            {"raw_skill": "LangGraph"},
            {"raw_skill": "Agent"},
        ],
        # 每个观测窗口四份 JD 中有两份提及，满足“低频但跨来源”的加分技能定义。
        "bonus_skills": ([{"raw_skill": "向量数据库"}] if (index - 1) % 4 < 2 else []),
        "industry": "人工智能",
        "business_scenarios": ["企业知识库", "流程自动化", "智能客服"],
    }
    for index, (title, published) in enumerate(zip(_DEMO_TITLES, _DEMO_DATES, strict=True), start=1)
)


def _seed_reviewed_jds(database) -> None:
    with database.session_factory() as session:
        for item in DEMO_JDS:
            jd = session.get(JobDescription, item["id"])
            if jd is None:
                jd = JobDescription(
                    id=item["id"],
                    source_type="curated_recent_sample",
                    source_name=item["source_name"],
                    title=item["title"],
                    raw_text=(
                        f"岗位职责：{'；'.join(item['responsibilities'])}。"
                        f"任职要求：{'、'.join(skill['raw_skill'] for skill in item['required_skills'])}。"
                    ),
                    cleaned_text=None,
                    publish_date=item["publish_date"],
                    parse_status="completed",
                    input_extraction_status="completed",
                    input_provider="jobpulse-202608-audit",
                )
                session.add(jd)
                session.flush()
            parsed = (
                session.query(JDParseResult).filter(JDParseResult.jd_id == item["id"]).one_or_none()
            )
            if parsed is None:
                parsed = JDParseResult(
                    jd_id=item["id"],
                    position_title=item["title"],
                    responsibilities=item["responsibilities"],
                    required_skills=item["required_skills"],
                    bonus_skills=item["bonus_skills"],
                    industry=item["industry"],
                    business_scenarios=item["business_scenarios"],
                    parse_confidence=0.98,
                    need_review=False,
                    schema_version="v2",
                    normalization_schema_version="v2",
                    workflow_status="reviewed",
                )
                session.add(parsed)
                session.flush()
            publication = (
                session.query(JDPublication).filter(JDPublication.jd_id == item["id"]).one_or_none()
            )
            if publication is None:
                publication = JDPublication(
                    id=f"demo-recent-agent-v6-pub-{item['id'][-3:]}",
                    parse_result_id=parsed.id,
                    jd_id=item["id"],
                    document_id=item["id"],
                    schema_version="v2",
                    normalization_schema_version="v2",
                    idempotency_key=f"emerging-discovery-recent-v6:{item['id']}",
                    published_by="emerging-discovery-recent-v6",
                    snapshot_payload={
                        "extraction_result": {
                            "company_facts": [
                                {
                                    "kind": "company_name",
                                    "value": item["company_name"],
                                }
                            ]
                        },
                        "jd": {
                            "title": item["title"],
                            "source_name": item["source_name"],
                            "source_type": "curated_recent_sample",
                            "enterprise_id": None,
                            "publish_date": item["publish_date"].isoformat(),
                        },
                        "legacy": {
                            "position_title": item["title"],
                            "responsibilities": item["responsibilities"],
                            "required_skills": item["required_skills"],
                            "bonus_skills": item["bonus_skills"],
                            "industry": item["industry"],
                            "business_scenarios": item["business_scenarios"],
                        },
                    },
                )
                session.add(publication)
        session.commit()


def _run(database, settings: Settings, request_id: str, start: date, end: date):
    with database.session_factory() as session:
        repository = SqlAlchemyDiscoveryRepository(session, allow_legacy_reviewed=False)
        demo_jd_ids = tuple(item["id"] for item in DEMO_JDS)
        facts = [fact for fact in repository.list_released_jd_facts() if fact.jd_id in demo_jd_ids]
        requests = build_rolling_discovery_requests(
            RunDiscoveryCommand(
                request_id=request_id,
                algorithm="multi_view",
                time_window_start=start,
                time_window_end=end,
                jd_ids=demo_jd_ids,
            ),
            facts,
            freeze(repository.discovery_config()),
        )
        client = EmergingDiscoveryClient(
            base_url=settings.EMERGING_DISCOVERY_BASE_URL,
            timeout=settings.EMERGING_DISCOVERY_TIMEOUT_SECONDS,
            token=settings.EMERGING_DISCOVERY_INTERNAL_TOKEN,
        )
        results = []
        for request in requests:
            payload = discovery_run_payload(request)
            payload["position_references"] = [
                {
                    "position_id": "LLM_ALGORITHM_ENGINEER",
                    "graph_version_id": "22",
                    "required_skills": [
                        {"raw_skill": "Python"},
                        {"raw_skill": "SQL"},
                    ],
                }
            ]
            result = discovery_run_result(client.create_run(payload))
            results.append(result)
            current_window = request.time_windows[-1]
            for item in result.clusters:
                if repository.get_cluster(item.cluster_id) is not None:
                    continue
                repository.add_cluster(
                    ClusterProjection(
                        cluster_id=item.cluster_id,
                        discovery_run_id=result.run_id,
                        cluster_name=item.cluster_name,
                        algorithm_version=result.algorithm_version,
                        sample_count=item.sample_count,
                        core_skills=item.core_skills,
                        representative_titles=item.representative_titles,
                        representative_jd_ids=item.representative_jd_ids,
                        stability_score=item.stability_score,
                        growth_score=0.0,
                        distance_from_existing_positions=item.distance_from_existing_positions,
                        discovery_run_status=result.status,
                        discovery_assessment=freeze(
                            {
                                **dict(item.germination_assessment),
                                "standard_position_comparison": dict(
                                    item.standard_position_comparison
                                ),
                                "explainability": dict(item.explainability),
                                "lineage_relations": [dict(x) for x in item.lineage_relations],
                                "input_quality_report": dict(result.input_quality_report),
                                "run_context": dict(result.run_context),
                                "request_id": result.request_id or request_id,
                                "run_id": result.run_id,
                                "input_fingerprint": result.input_fingerprint,
                            }
                        ),
                        generated_definition=item.generated_definition,
                        discovery_lineages=result.lineages,
                        time_window_start=current_window.start,
                        time_window_end=current_window.end,
                    )
                )
            session.commit()
    result = results[-1]
    return {
        "run_id": result.run_id,
        "run_ids": [item.run_id for item in results],
        "observation_window_ids": [request.current_observation_window_id for request in requests],
        "status": result.status,
        "cluster_count": sum(len(item.clusters) for item in results),
        "candidate_service": result.provider,
    }


def _govern_and_publish_demo(settings: Settings) -> dict[str, object]:
    """Replay the recorded demo review through the normal governance handlers.

    The candidate and lifecycle must already exist in emerging-discovery; this
    function never creates a synthetic candidate or bypasses a release gate.
    """
    client = EmergingDiscoveryClient(
        base_url=settings.EMERGING_DISCOVERY_BASE_URL,
        timeout=settings.EMERGING_DISCOVERY_TIMEOUT_SECONDS,
        token=settings.EMERGING_DISCOVERY_INTERNAL_TOKEN,
    )
    payload = client.list_candidates(status="stable_emerging_role")
    candidates = [
        item
        for item in payload.get("candidates", [])
        if isinstance(item, dict)
        and any(
            str(jd_id).startswith("demo-recent-agent-v6-jd-")
            for jd_id in (item.get("identity_profile") or {}).get("member_jd_ids", [])
        )
    ]
    if not candidates:
        raise RuntimeError("The formal discovery chain did not produce the demo candidate")
    candidate = max(
        candidates,
        key=lambda item: (
            float(item.get("emergence_score") or 0.0),
            len((item.get("identity_profile") or {}).get("observed_window_ids", [])),
        ),
    )
    cluster_id = str(candidate.get("current_cluster_id") or "")
    if not cluster_id:
        raise RuntimeError("The stable demo candidate has no current projected cluster")

    runtime = _build_runtime(settings)
    actor = EmergingActor("demo-data-review-replay", "admin")
    try:
        handlers = runtime.container.emerging_positions
        record = handlers.create.execute(
            cluster_id,
            actor,
            lifecycle_context={
                "candidate_id": candidate.get("candidate_id"),
                "status": candidate.get("status"),
                "emergence_score": candidate.get("emergence_score"),
                "observed_window_ids": list(
                    (candidate.get("identity_profile") or {}).get(
                        "observed_window_ids", []
                    )
                ),
                "support_count": candidate.get("support_count"),
                "company_coverage": candidate.get("company_coverage"),
                "data_provenance": {
                    "kind": "versioned_demo_fixture",
                    "dataset": "emerging-discovery-recent.v6",
                    "source_type": "curated_recent_sample",
                },
            },
        )
        status = record.candidate.status.value
        if status in {"draft", "rejected"}:
            record = handlers.submit_review.execute(record.candidate.candidate_id, actor)
            status = record.candidate.status.value
        if status == "pending_review":
            record = handlers.review.execute(
                record.candidate.candidate_id,
                ReviewEmergingDefinitionCommand(
                    conclusion="approved",
                    reason=(
                        "Replay of the versioned competition demo review; all claims "
                        "remain bound to the packaged JD evidence."
                    ),
                ),
                actor,
            )
            status = record.candidate.status.value
        if status == "approved":
            record = handlers.publish.execute(record.candidate.candidate_id, actor)
            status = record.candidate.status.value
        if status != "published":
            raise RuntimeError(f"Demo candidate stopped at unexpected status: {status}")
        return {
            "candidate_id": candidate.get("candidate_id"),
            "cluster_id": cluster_id,
            "emerging_id": record.candidate.candidate_id,
            "status": status,
            "review_mode": "versioned_demo_review_replay",
        }
    finally:
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Only insert the reviewed JD snapshots"
    )
    args = parser.parse_args()

    settings = Settings()
    database = create_database(settings.DATABASE_URL)
    try:
        _seed_reviewed_jds(database)
        runs = []
        if not args.dry_run:
            runs = [
                _run(
                    database,
                    settings,
                    "demo-emerging-agent-recent-windows-v6",
                    date(2026, 7, 27),
                    date(2026, 8, 8),
                ),
            ]
        governance = None
        if not args.dry_run:
            governance = _govern_and_publish_demo(settings)
        print(
            json.dumps(
                {
                    "dataset": "emerging-discovery-recent.v6",
                    "data_period": {"start": "2026-07-27", "end": "2026-08-08"},
                    "jd_count": len(DEMO_JDS),
                    "governance": governance,
                    "runs": runs,
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
        return 0
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
