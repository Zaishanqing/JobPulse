def assert_error(response,status):
    body=response.json(); assert response.status_code==status
    assert set(("code","message","data","details","trace_id"))<=body.keys()
    assert body["data"] is None and body["trace_id"].startswith("req_")

def test_http_validation_and_constraint_errors_have_one_contract(client,auth_headers):
    assert_error(client.get("/api/v1/jds/nope"),401)
    assert_error(client.post("/api/v1/jds",json={"raw_text":""},headers=auth_headers("personal_user")),403)
    assert_error(client.post("/api/v1/jds",json={"source_credibility":2},headers=auth_headers()),422)
    assert client.post("/api/v1/jds",json={"document_id":"DUP","raw_text":"x"},headers=auth_headers()).status_code==200
    assert_error(client.post("/api/v1/jds",json={"document_id":"DUP","raw_text":"x"},headers=auth_headers()),409)

def test_success_body_also_contains_trace(client):
    body=client.get("/health").json(); assert body["code"]==0 and body["trace_id"].startswith("req_")
