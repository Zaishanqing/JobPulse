from scripts.evaluate_requirement_inflation import build_evaluation


def test_evaluation_compares_raw_and_calibrated_requirements():
    report = build_evaluation(
        [
            {
                "data": {
                    "position_id": "BACKEND_ENGINEER",
                    "graph_version": "v1",
                    "requirement_inflation": {
                        "summary": {
                            "total_required_requirement_count": 4,
                            "market_supported_count": 2,
                            "enterprise_specific_count": 1,
                            "inflation_risk_count": 1,
                        },
                        "jd_diagnostics": [
                            {
                                "requirements": [
                                    {
                                        "market": {
                                            "leave_one_out_enterprise_count": count
                                        }
                                    }
                                    for count in (2, 1, 0, 0)
                                ]
                            }
                        ],
                    },
                }
            }
        ]
    )

    row = report["positions"][0]
    assert row["raw_required_requirement_count"] == 4
    assert row["calibrated_required_requirement_count"] == 2
    assert row["inflation_suppression_ratio"] == 0.25
    assert row["cross_enterprise_supported_ratio"] == 0.5
    assert report["aggregate"]["cross_enterprise_supported_count"] == 2
