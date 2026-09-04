from sqlalchemy import select

from app.infrastructure.sqlalchemy.graph_persistence import (
    build_summary_status,
    publish_gate_status,
)
from app.infrastructure.sqlalchemy.query_base import QuerySession, position_build_version
from app.models import (
    GraphBuildRun,
    GraphBuildJob,
    GraphBuildSample,
)


def _summary_contract(summary: dict) -> dict:
    """Expose legacy A-DATA-01 build summaries in the portal summary contract."""
    summary = dict(summary)
    if "included_samples" not in summary:
        summary["included_samples"] = int(summary.get("sample_count") or 0)
    if "relations" not in summary:
        summary["relations"] = int(summary.get("skill_count") or 0)
    summary.setdefault("excluded_samples", 0)
    return summary


class BuildQueryMixin(QuerySession):
    def build_job(self, job_id: int) -> dict | None:
        row = self.session.get(GraphBuildJob, job_id)
        if row is None:
            return None
        build = self.session.get(GraphBuildRun, row.build_run_id) if row.build_run_id else None
        return {
            "job_id": row.id,
            "job_key": row.job_key,
            "position_id": row.position_id,
            "status": row.status,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "build_run_id": row.build_run_id,
            "build_version": (
                position_build_version(self.session, build) if build else None
            ),
            "summary": (
                _summary_contract(build_summary_status(self.session, build))
                if build
                else None
            ),
            "error": (
                {"code": row.error_code, "message": row.error_message}
                if row.error_code
                else None
            ),
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
    def build_runs(self, position_id: str) -> list[dict]:
        rows = self.session.scalars(
            select(GraphBuildRun)
            .where(GraphBuildRun.position_id == position_id)
            .order_by(GraphBuildRun.id.desc())
        ).all()
        return [
            {
                "id": row.id,
                "build_version": position_build_version(self.session, row),
                "status": row.status,
                "summary": _summary_contract(
                    build_summary_status(self.session, row)
                ),
            }
            for row in rows
        ]

    def build_run(self, run_id: int) -> dict | None:
        row = self.session.get(GraphBuildRun, run_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "build_version": position_build_version(self.session, row),
            "position_id": row.position_id,
            "status": row.status,
            "window_start": row.window_start,
            "window_end": row.window_end,
            "config_snapshot": row.config_snapshot,
            "summary": _summary_contract(
                build_summary_status(self.session, row)
            ),
        }

    def build_samples(self, run_id: int) -> list[dict] | None:
        if self.session.get(GraphBuildRun, run_id) is None:
            return None
        rows = self.session.scalars(
            select(GraphBuildSample).where(GraphBuildSample.build_run_id == run_id)
        ).all()
        return [
            {
                "document_id": row.document_id,
                "included": row.included,
                "exclusion_reasons": row.exclusion_reasons,
                "effective_weight": row.effective_weight,
            }
            for row in rows
        ]

    def publish_gate(self, run_id: int) -> dict | None:
        run = self.session.get(GraphBuildRun, run_id)
        if run is None:
            return None
        return publish_gate_status(self.session, run)
