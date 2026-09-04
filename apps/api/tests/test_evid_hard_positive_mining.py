"""Tests for the EVID hard-positive candidate mining channels."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from app.contexts.evidence_independence.contracts import EvidenceRecord


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_evid_hard_pair_benchmark.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "build_evid_hard_pair_benchmark", _SCRIPT
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


def _record(
    evidence_id: str,
    *,
    source: str = "boss",
    enterprise: str | None = "ent-a",
    published: date | None = date(2026, 7, 1),
    template: str | None = None,
    text: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject_ref="BACKEND_ENGINEER",
        source_id=source,
        enterprise_id=enterprise,
        position_id="BACKEND_ENGINEER",
        published_at=published,
        template_cluster_id=template,
        release_id="release-1",
        text=text,
    )


def test_hard_positive_mining_adds_independent_channels() -> None:
    records = [
        _record(
            "rewrite-1",
            enterprise="ent-c",
            published=date(2026, 1, 1),
            text="Java 高级工程师 负责支付系统开发与维护",
        ),
        _record(
            "rewrite-2",
            enterprise="ent-c",
            published=date(2026, 7, 1),
            text="Java 高级工程师 负责支付平台架构与稳定性保障",
        ),
        _record(
            "missing-ent-1",
            source="boss",
            enterprise=None,
            text="Python 后端工程师 负责数据服务",
        ),
        _record(
            "missing-ent-2",
            source="liepin",
            enterprise="ent-b",
            text="Python 后端工程师 负责数据平台",
        ),
        _record(
            "time-1",
            enterprise="ent-a",
            published=None,
            text="Go 服务端工程师 负责高并发网关",
        ),
        _record(
            "time-2",
            enterprise="ent-e",
            text="Go 服务端工程师 负责高并发网关",
        ),
        _record(
            "template-1",
            enterprise="ent-d",
            template="tpl-9",
            published=date(2026, 1, 1),
            text="前端工程师 React 组件开发",
        ),
        _record(
            "template-2",
            enterprise="ent-d",
            template="tpl-9",
            published=date(2026, 7, 1),
            text="前端工程师 React 组件与交互开发",
        ),
    ]
    pairs = _MODULE._mine_positive(
        records,
        (),
        "BACKEND_ENGINEER",
        limit=100,
    )
    kinds = {pair["kind"] for pair in pairs}
    assert "D_major_rewrite_same_event" in kinds
    assert "F_enterprise_missing_cross_source_repost" in kinds
    assert "G_time_missing_duplicate" in kinds
    assert "H_template_neighbour_repost" in kinds
    assert all(pair["candidate_label"] is True for pair in pairs)
