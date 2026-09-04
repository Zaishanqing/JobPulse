def trend_change_payload() -> dict:
    return {
        "request_id": "trend-change-request-1",
        "subjects": [
            {
                "subject_id": "subject-rising",
                "subject_type": "market_signal",
                "windows": [
                    {"window": f"w{index + 1}", "score": score}
                    for index, score in enumerate([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
                ],
            },
            {
                "subject_id": "subject-stable",
                "subject_type": "market_signal",
                "windows": [
                    {"window": f"w{index + 1}", "score": score}
                    for index, score in enumerate([0.40, 0.41, 0.39, 0.40, 0.41])
                ],
            },
        ],
    }


def test_trend_change_analysis_create_get_and_filters(client, auth):
    created = client.post(
        "/internal/v1/trend-change/analyses",
        headers=auth,
        json=trend_change_payload(),
    )
    assert created.status_code == 201
    data = created.json()["data"]
    analysis_id = data["analysis_id"]
    assert data["algorithm_version"] == "trend-change.v1"
    assert len(data["subjects"]) == 2

    fetched = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}",
        headers=auth,
        params={"subject_id": "subject-rising"},
    )
    assert fetched.status_code == 200
    subjects = fetched.json()["data"]["subjects"]
    assert [subject["subject_id"] for subject in subjects] == ["subject-rising"]
    assert subjects[0]["trend_state"] == "rising"

    by_state = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}",
        headers=auth,
        params={"trend_state": "stable"},
    )
    assert [subject["subject_id"] for subject in by_state.json()["data"]["subjects"]] == [
        "subject-stable"
    ]

    by_window = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}",
        headers=auth,
        params={"subject_id": "subject-rising", "window": "w4"},
    )
    assert [
        item["window"] for item in by_window.json()["data"]["subjects"][0]["windows"]
    ] == ["w4"]


def test_trend_change_change_points_endpoint_and_404(client, auth):
    created = client.post(
        "/internal/v1/trend-change/analyses",
        headers=auth,
        json=trend_change_payload(),
    )
    analysis_id = created.json()["data"]["analysis_id"]

    response = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}/change-points",
        headers=auth,
        params={"subject_id": "subject-rising"},
    )
    assert response.status_code == 200
    points = response.json()["data"]
    assert [point["change_point_window"] for point in points] == ["w4"]

    assert client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}/change-points",
        headers=auth,
        params={"subject_id": "subject-stable"},
    ).json()["data"] == []

    missing = client.get(
        "/internal/v1/trend-change/analyses/does-not-exist",
        headers=auth,
    )
    assert missing.status_code == 404


def test_trend_change_validation_rejects_duplicate_windows(client, auth):
    payload = trend_change_payload()
    payload["subjects"][0]["windows"][0]["window"] = "w2"

    response = client.post(
        "/internal/v1/trend-change/analyses",
        headers=auth,
        json=payload,
    )
    assert response.status_code == 422
