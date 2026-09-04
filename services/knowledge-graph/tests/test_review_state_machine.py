from sqlalchemy import select

from app.models import AuditLog, ReviewTask

def test_review_state_machine_and_actor_source(client,db,auth_headers,users):
    task=ReviewTask(object_type="evidence",object_id="1"); db.add(task); db.commit(); reviewer=auth_headers("reviewer")
    assert client.post(f"/api/v1/review-tasks/{task.id}/approve",json={"reason":"premature"},headers=reviewer).status_code==409
    assert client.post(f"/api/v1/review-tasks/{task.id}/claim",json={"reason":"take ownership","actor_id":999},headers=reviewer).status_code==200
    assert client.post(f"/api/v1/review-tasks/{task.id}/claim",json={"reason":"duplicate"},headers=auth_headers("admin")).status_code==409
    assert client.post(f"/api/v1/review-tasks/{task.id}/modify",json={"reason":"record findings","payload":{"checked":True}},headers=reviewer).status_code==200
    assert client.post(f"/api/v1/review-tasks/{task.id}/approve",json={"reason":"exact"},headers=reviewer).status_code==200
    assert client.post(f"/api/v1/review-tasks/{task.id}/modify",json={"reason":"too late"},headers=reviewer).status_code==409
    audit=db.scalar(select(AuditLog).where(AuditLog.action=="review_claim")); assert audit.actor_id==users["reviewer"].id

def test_ordinary_user_cannot_review(client,db,auth_headers):
    task=ReviewTask(object_type="evidence",object_id="1"); db.add(task); db.commit()
    assert client.post(f"/api/v1/review-tasks/{task.id}/claim",json={"reason":"unauthorized"},headers=auth_headers("personal_user")).status_code==403
