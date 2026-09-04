from datetime import datetime,timezone

def test_health_and_crud(client,auth_headers):
    headers=auth_headers()
    assert client.get("/health").json()["data"]["providers"]["neo4j"]["status"]=="disabled"
    r=client.post("/api/v1/jds",json={"document_id":"JD1","raw_text":"后端工程师\n熟悉 Python"},headers=headers); assert r.status_code==200 and r.json()["trace_id"].startswith("req_")
    assert client.get("/api/v1/jds/JD1",headers=headers).json()["data"]["raw_text"].endswith("Python")
    assert len(client.get("/api/v1/jds",headers=headers).json()["data"])==1
def test_import_align_normalize_quality(client,auth_headers):
    headers=auth_headers(); client.post("/api/v1/jds",json={"document_id":"JD1","raw_text":"后端工程师\n熟悉 Python，SQL\n本科\n团队合作\n科技企业\n月薪20k-30k"},headers=headers)
    payload={"document_id":"JD1","job_title":{"text":"后端工程师","evidence":{"source_id":"JD1","quote":"后端工程师"}},"responsibilities":[],"requirements":[{"requirement_id":"r1","kind":"skill","modality":"required","evidence":{"source_id":"JD1","quote":"熟悉 Python，SQL"},"items":[{"name":"Python"},{"name":"SQL"}]},{"requirement_id":"r2","kind":"education","modality":"required","evidence":{"source_id":"JD1","quote":"本科"},"text":"本科"},{"requirement_id":"r3","kind":"soft_skill","modality":"unknown","evidence":{"source_id":"JD1","quote":"团队合作"},"text":"团队合作"}],"company_facts":[{"fact_id":"c1","text":"科技企业","evidence":{"source_id":"JD1","quote":"科技企业"}}],"employment_facts":[{"fact_id":"e1","fact_type":"salary","text":"月薪20k-30k","evidence":{"source_id":"JD1","quote":"月薪20k-30k"}}]}
    assert client.post("/api/v1/jds/JD1/extraction-result/import",json=payload,headers=headers).status_code==200
    aligned=client.post("/api/v1/jds/JD1/extraction-result/align",headers=headers).json()["data"]; assert aligned["requirements"][0]["evidence"]["alignment"]=="exact"
    normalized=client.post("/api/v1/jds/JD1/normalize",headers=headers).json()["data"]; assert len(normalized["normalized_requirements"][0]["normalized_skills"])==2
    quality=client.post("/api/v1/jds/JD1/duplicate-check",json={},headers=headers).json()["data"]; assert .05<=quality["effective_sample_weight"]<=1
    assert len(client.get("/api/v1/jds/JD1/evidence",headers=headers).json()["data"])>=3
def test_parse_compatibility(client,auth_headers):
    headers=auth_headers(); client.post("/api/v1/jds",json={"document_id":"JD2","raw_text":"数据分析师\nSQL"},headers=headers)
    assert client.post("/api/v1/jds/JD2/parse",headers=headers).status_code==200
    result=client.get("/api/v1/jds/JD2/parse-result",headers=headers).json()["data"]; assert result["document_id"]=="JD2" and "requirements" in result
def test_schema_and_missing(client,auth_headers):
    assert "oneOf" in str(client.get("/api/v1/schemas/jd-extraction-v2.json").json())
    assert client.get("/api/v1/jds/nope",headers=auth_headers()).status_code==404
