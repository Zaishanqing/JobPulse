from datetime import datetime, timezone

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from src.application.mappers import envelope_to_pipeline_input
from src.preprocess import normalize_jd_text


def test_envelope_mapper_preserves_raw_text_and_reproduces_pipeline_nfkc_input():
    raw_text = "使⽤ＡＩ①开发；熟练使用 Python。"
    envelope = CrawlerJDEnvelopeV1(
        source_record_id="job-1",
        source_platform="boss_zhipin",
        crawl_time=datetime.now(timezone.utc),
        raw_text=raw_text,
        raw_payload={},
        text_canonicalization_version="v1",
        source_version="1",
    )
    mapped = envelope_to_pipeline_input(envelope, "doc-1")
    assert mapped["jd_text"] == normalize_jd_text(raw_text)
    assert mapped["jd_text_original"] == raw_text
    for block in mapped["source_blocks"]:
        assert mapped["jd_text"][block["start"] : block["end"]] == block["text"]
