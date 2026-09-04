import pytest
from pydantic import ValidationError
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import NormalizedSkill
from app.domain.policies import align_extraction, align_quote, normalize_key
from app.infrastructure.providers.normalization import Normalizer, normalize_salary
from app.domain.policies import effective_weight, quality_scores

def ev(q="Python"): return {"source_id":"JD1","quote":q,"alignment":"unresolved"}
def extraction(requirements=None):
    return JDExtractionResult(document_id="JD1",job_title={"text":"后端工程师","evidence":ev("后端工程师")},requirements=requirements or [])
def test_discriminated_union_and_no_skill_id():
    x=extraction([{"requirement_id":"r1","kind":"skill","modality":"required","evidence":ev(),"items":[{"name":"Python"}]}])
    assert x.requirements[0].kind=="skill" and "skill_id" not in x.model_dump_json()
    with pytest.raises(ValidationError): JDExtractionResult.model_validate({**x.model_dump(),"skill_id":"bad"})
def test_each_requirement_kind():
    reqs=[]
    for i,kind in enumerate(["education","experience","certificate","soft_skill","other"]): reqs.append({"requirement_id":str(i),"kind":kind,"modality":"unknown","evidence":ev(kind),"text":kind})
    assert [r.kind for r in extraction(reqs).requirements]==["education","experience","certificate","soft_skill","other"]
def test_company_and_employment_are_separate():
    x=JDExtractionResult(document_id="JD1",company_facts=[{"fact_id":"c","text":"创业公司","evidence":ev("创业公司")}],employment_facts=[{"fact_id":"e","fact_type":"location","text":"上海","evidence":ev("上海")}])
    assert x.company_facts[0].text!="上海" and x.employment_facts[0].fact_type=="location"
def test_exact_alignment_and_occurrences():
    raw="Python and Python"; assert align_quote(raw,"Python",1)=={"start":11,"end":17,"alignment":"exact","occurrence_index":1}
    assert align_quote(raw,"Java")["alignment"]=="unresolved"
    p={"evidence":ev()}; aligned=align_extraction(raw,p); assert raw[aligned["evidence"]["start"]:aligned["evidence"]["end"]]=="Python"
def test_normalizer_exact_only_and_source_name():
    x=extraction([{"requirement_id":"r1","kind":"skill","modality":"required","evidence":ev(),"items":[{"name":" ＰＹＴＨＯＮ "},{"name":"Pythn"}]}])
    n=Normalizer().normalize(x); skills=n.normalized_requirements[0].normalized_skills
    assert skills[0].resolution_status=="unresolved" and skills[0].skill_id is None
    assert skills[0].source_name==" ＰＹＴＨＯＮ "
    assert skills[1].resolution_status=="unresolved" and skills[1].skill_id is None
def test_normalization_key_and_weight_clamp():
    assert normalize_key(" Ｐｙｔｈｏｎ  SDK ")=="python sdk"
    assert effective_weight(1,0,0,0)==1 and effective_weight(0,1,1,1)==.05
def test_normalized_skill_contract():
    x=NormalizedSkill(source_name="X",resolution_status="unresolved"); assert x.source_name=="X" and x.skill_id is None

def test_nested_v2_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        extraction([{"requirement_id":"r1","kind":"skill","modality":"required","evidence":{**ev(),"unexpected":True},"items":[{"name":"Python"}]}])
    with pytest.raises(ValidationError): NormalizedSkill(source_name="X",resolution_status="unresolved",unexpected=True)

def test_salary_normalization_period_currency_and_ranges():
    monthly=normalize_salary("月薪 20k-30k"); assert (monthly.minimum,monthly.maximum,monthly.period)==(20000,30000,"month")
    yearly=normalize_salary("年薪 30万-50万"); assert (yearly.minimum,yearly.maximum,yearly.period)==(300000,500000,"year")
    hourly=normalize_salary("USD $50/hour"); assert hourly.currency=="USD" and hourly.period=="hour" and hourly.minimum==hourly.maximum==50
    unknown=normalize_salary("面议"); assert unknown.minimum is None and unknown.period=="unknown"

def test_quality_and_alignment_empty_collection_branches():
    assert quality_scores("plain text")["duplicate_score"]==0
    payload={"items":[{"value":"no evidence"},ev("missing")]}; result=align_extraction("raw",payload)
    assert result["items"][1]["alignment"]=="unresolved"
