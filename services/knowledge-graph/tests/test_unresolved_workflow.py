from sqlalchemy import func, select

from app.models import NormalizedSkillRecord, Skill, SkillAlias, UnresolvedNormalizationItem
from tests.factories import prepare_catalog, prepare_jd

def unresolved_skill(db): return db.scalar(select(UnresolvedNormalizationItem).where(UnresolvedNormalizationItem.item_type=="skill"))

def test_unresolved_items_expose_real_or_synthetic_source(client,db,auth_headers):
    prepare_catalog(db); prepare_jd(client,auth_headers(),skill="Mystery")
    item=client.get("/api/v1/normalization/unresolved-items",headers=auth_headers("reviewer")).json()["data"][0]
    assert item["source"]["source_type"]=="test"
    assert item["source"]["is_synthetic"] is False

def test_resolve_existing_skill_updates_normalized_record_and_builds(client,db,auth_headers):
    prepare_catalog(db); prepare_jd(client,auth_headers(),skill="Mystery")
    item=unresolved_skill(db); response=client.post(f"/api/v1/normalization/unresolved-items/{item.id}/resolve",json={"payload":{"skill_id":"SKILL_PYTHON"},"reason":"same competency","actor_id":999},headers=auth_headers("reviewer"))
    assert response.status_code==200 and response.json()["data"]["status"]=="resolved_existing_skill"
    normalized=db.scalar(select(NormalizedSkillRecord).where(NormalizedSkillRecord.source_name=="Mystery"))
    assert normalized.skill_id=="SKILL_PYTHON" and normalized.resolution_status=="manually_confirmed"
    build=client.post("/api/v1/positions/BACKEND_ENGINEER/graph/build",json={},headers=auth_headers())
    assert build.json()["data"]["summary"]["relations"]==1

def test_create_skill_is_real_and_atomic(client,db,auth_headers):
    prepare_catalog(db); prepare_jd(client,auth_headers(),skill="Mystery")
    item=unresolved_skill(db); before=db.scalar(select(func.count()).select_from(Skill))
    response=client.post(f"/api/v1/normalization/unresolved-items/{item.id}/create-skill",json={"reason":"catalog review","payload":{"canonical_name":"Mystery","category_code":"LANG"}},headers=auth_headers("reviewer"))
    assert response.status_code==200 and db.scalar(select(func.count()).select_from(Skill))==before+1
    normalized=db.scalar(select(NormalizedSkillRecord).where(NormalizedSkillRecord.source_name=="Mystery"))
    assert normalized.skill_id==response.json()["data"]["skill_id"] and normalized.resolution_status=="manually_confirmed"

def test_create_skill_failure_rolls_back_everything(client,db,auth_headers):
    prepare_catalog(db); db.add(SkillAlias(skill_id="SKILL_PYTHON",alias="taken")); db.commit(); prepare_jd(client,auth_headers(),skill="Mystery")
    item=unresolved_skill(db); before=db.scalar(select(func.count()).select_from(Skill))
    response=client.post(f"/api/v1/normalization/unresolved-items/{item.id}/create-skill",json={"reason":"catalog review","payload":{"canonical_name":"Mystery","category_code":"LANG","alias":"taken"}},headers=auth_headers("reviewer"))
    assert response.status_code==409 and db.scalar(select(func.count()).select_from(Skill))==before
    db.refresh(item); assert item.status=="open"
