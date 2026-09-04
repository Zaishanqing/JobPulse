from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.source_jds import get_source_jd_use_cases
from app.contexts.access import AccountRecord
from app.contexts.source_jds import (
    InvalidSourceJDEnvelope,
    SourceJDImportConflict,
    SourceJDNotFound,
    SourceJDUseCases,
)
from app.core.response import success_response
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from app.contexts.source_jds.ports import SourceJDVersionRecord
from app.domain.json_types import thaw_json_object


router = APIRouter(tags=["source-jds"])


@router.post("/source-jds/import")
def import_source_jd(
    payload: CrawlerJDEnvelopeV1,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: SourceJDUseCases = Depends(get_source_jd_use_cases),
):
    del current_user
    try:
        return success_response(data=asdict(use_cases.import_source_jd(payload)))
    except InvalidSourceJDEnvelope as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceJDImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/source-jds/{source_jd_id}")
def get_source_jd(
    source_jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: SourceJDUseCases = Depends(get_source_jd_use_cases),
):
    del current_user
    try:
        return success_response(data=asdict(use_cases.get_source_jd(source_jd_id)))
    except SourceJDNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/source-jds/{source_jd_id}/versions")
def list_source_jd_versions(
    source_jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: SourceJDUseCases = Depends(get_source_jd_use_cases),
):
    del current_user
    try:
        return success_response(
            data=[_version_data(item) for item in use_cases.list_versions(source_jd_id)]
        )
    except SourceJDNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/source-jd-versions/{version_id}")
def get_source_jd_version(
    version_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: SourceJDUseCases = Depends(get_source_jd_use_cases),
):
    del current_user
    try:
        return success_response(data=_version_data(use_cases.get_version(version_id)))
    except SourceJDNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _version_data(value: SourceJDVersionRecord) -> dict[str, object]:
    data = asdict(value)
    data["raw_payload"] = thaw_json_object(value.raw_payload)
    return data
