from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = FRAMEWORK_ROOT.parent
CV_EXTRACTION_ROOT = REPOSITORY_ROOT / "Extraction" / "cvextraction"
sys.path.insert(0, str(FRAMEWORK_ROOT))
sys.path.insert(0, str(CV_EXTRACTION_ROOT))

from api.demo_snapshots import DemoCVSnapshotService  # noqa: E402
from api.config import Settings as CVSettings  # noqa: E402
from app.infrastructure.extraction_tasks import (  # noqa: E402
    ExtractionProviderError,
    HttpJDExtractionProvider,
    RuleBasedJDExtractionProvider,
)
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1  # noqa: E402


def main() -> None:
    raw_text = "职位：Python 工程师\n负责 Python 后端开发"
    envelope = CrawlerJDEnvelopeV1(
        source_platform="smoke",
        source_record_id="explicit-mode-1",
        source_url="https://example.test/jobs/explicit-mode-1",
        crawl_time=datetime.now(timezone.utc),
        raw_text=raw_text,
        raw_payload={"text": raw_text},
        text_canonicalization_version="raw-v1",
        source_version="1",
    )

    rule_bundle = RuleBasedJDExtractionProvider().extract(envelope)
    assert rule_bundle.schema_version == "extracted-jd-bundle-v2"
    assert rule_bundle.execution is not None
    assert rule_bundle.execution.mode == "rule"
    assert rule_bundle.need_review is True
    assert rule_bundle.confidence_level == "limited"
    assert any(
        flag.get("code") == "RULE_BASED_EXTRACTION_REQUIRES_REVIEW"
        for flag in rule_bundle.review_flags
    )

    try:
        HttpJDExtractionProvider(None, None, 1, 2).extract(envelope)
    except ExtractionProviderError as exc:
        assert exc.code == "extraction_provider_not_configured"
    else:
        raise AssertionError("unconfigured LLM provider must fail without fallback")

    demo_settings = CVSettings.model_construct(
        CV_EXTRACTION_DEMO_SNAPSHOT_DIR=str(
            CV_EXTRACTION_ROOT / "resources" / "demo_snapshots"
        )
    )
    demo = DemoCVSnapshotService(demo_settings).get("jobgraph-demo-cv.v1")
    assert demo["execution"]["mode"] == "demo_snapshot"
    assert demo["execution"]["provider"] == "jobgraph_demo_data"
    assert demo["execution"]["is_demo"] is True
    assert demo["execution"]["dataset_version"] == "jobgraph-demo-cv.v1"

    print("extraction modes smoke: OK")


if __name__ == "__main__":
    main()
