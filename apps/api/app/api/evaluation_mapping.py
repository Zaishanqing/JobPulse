from app.contexts.evaluation import EvaluationReportRecord


def evaluation_report_data(report: EvaluationReportRecord) -> dict[str, object]:
    return {
        "report_id": report.report_id,
        "report_type": report.report_type,
        "dataset_id": report.dataset_id,
        "metrics": report.metrics.as_dict(),
        "error_cases": [item.as_dict() for item in report.error_cases],
        "evaluation_status": report.evaluation_status,
        "algorithm_version": report.algorithm_version,
        "config_snapshot": report.config_snapshot.as_dict(),
        "evaluated_count": report.evaluated_count,
        "error_count": report.error_count,
        "implementation_status": "data_driven_rule_evaluation",
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }
