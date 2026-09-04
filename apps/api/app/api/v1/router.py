from fastapi import APIRouter

from app.api.v1.reserved import router as reserved_router
from app.api.v1.auth import router as auth_router
from app.api.v1.account_admin import router as account_admin_router
from app.api.v1.enterprise_jobs import router as enterprise_jobs_router
from app.api.v1.enterprise_candidates import (
    published_jobs_router,
    router as enterprise_candidates_router,
)
from app.api.v1.enterprises import router as enterprises_router
from app.api.v1.emerging_positions import router as emerging_positions_router
from app.api.v1.emerging_config import router as emerging_config_router
from app.api.v1.evaluation import router as evaluation_router
from app.api.v1.evidence_sources import router as evidence_sources_router
from app.api.v1.embeddings import router as embeddings_router
from app.api.v1.files import router as files_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.jds import router as jds_router
from app.api.v1.learning_paths import router as learning_paths_router
from app.api.v1.matches import router as matches_router
from app.api.v1.observability import router as observability_router
from app.api.v1.ocr import router as ocr_router
from app.api.v1.position_clusters import router as position_clusters_router
from app.api.v1.positions import router as positions_router
from app.api.v1.predicted_positions import router as predicted_positions_router
from app.api.v1.evidence import router as evidence_router
from app.api.v1.resumes import router as resumes_router
from app.api.v1.review_tasks import router as review_tasks_router
from app.api.v1.skills import router as skills_router
from app.api.v1.system import router as system_router
from app.api.v1.trend_reports import router as trend_reports_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.knowledge_graph import router as knowledge_graph_router
from app.api.v1.portal import router as portal_router
from app.api.v1.source_jds import router as source_jds_router
from app.api.v1.extraction_tasks import router as extraction_tasks_router
from app.api.v1.jd_parse_results import router as jd_parse_results_router
from app.api.v1.outbox_events import router as outbox_events_router
from app.api.v1.cv_ingestion import router as cv_ingestion_router
from app.api.v1.matching_contracts import router as matching_contracts_router
from app.api.v1.rag import router as rag_router
from app.api.v1.insights import router as insights_router
from app.api.v1.acquisition import router as acquisition_router

router = APIRouter()
router.include_router(reserved_router)
router.include_router(observability_router)
router.include_router(ocr_router)
router.include_router(auth_router)
router.include_router(account_admin_router)
router.include_router(enterprises_router)
router.include_router(enterprise_jobs_router)
router.include_router(enterprise_candidates_router)
router.include_router(published_jobs_router)
router.include_router(jds_router)
router.include_router(skills_router)
router.include_router(files_router)
router.include_router(feedback_router)
router.include_router(resumes_router)
router.include_router(position_clusters_router)
router.include_router(emerging_config_router)
router.include_router(emerging_positions_router)
router.include_router(trend_reports_router)
router.include_router(evaluation_router)
router.include_router(system_router)
router.include_router(tasks_router)
router.include_router(positions_router)
router.include_router(matches_router)
router.include_router(learning_paths_router)
router.include_router(evidence_sources_router)
router.include_router(embeddings_router)
router.include_router(review_tasks_router)
router.include_router(evidence_router)
router.include_router(knowledge_graph_router)
router.include_router(portal_router)
router.include_router(predicted_positions_router)
router.include_router(source_jds_router)
router.include_router(extraction_tasks_router)
router.include_router(jd_parse_results_router)
router.include_router(outbox_events_router)
router.include_router(cv_ingestion_router)
router.include_router(matching_contracts_router)
router.include_router(rag_router)
router.include_router(insights_router)
router.include_router(acquisition_router)
