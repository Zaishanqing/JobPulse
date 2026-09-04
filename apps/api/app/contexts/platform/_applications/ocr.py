from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from app.domain.accounts import AccountActor
from app.domain.ocr import OCR_REVIEW_ROLES
from app.domain.tasks import utc_now
from app.contexts.platform._ports.ocr import OCRExtractionPort, OCRResultRecord, OCRUnitOfWork
from app.contexts.tasks import TaskLog, TaskPayload, TaskRecord, TaskWorkflowPort
from app.domain.errors import PermissionDenied


class OCRResultNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageOCR:
    uow_factory: Callable[[], OCRUnitOfWork]
    extractor: OCRExtractionPort
    tasks: TaskWorkflowPort

    def run(self, actor: AccountActor, source_type: str, filename: str | None, content: bytes, media_type: str) -> tuple[OCRResultRecord, TaskRecord]:
        outcome = self.extractor.extract(content, media_type)
        with self.uow_factory() as uow:
            result = uow.ocr.add(source_type, filename, outcome, actor.account_id)
            task = self._task(actor, source_type, filename, media_type, result)
            uow.add_task(task)
            uow.commit()
            return result, task

    def task(self, actor: AccountActor, task_id: str) -> TaskRecord:
        return self.tasks.get(actor, task_id, {"ocr_image", "ocr_pdf"})

    def update(self, actor: AccountActor, result_id: str, text: str) -> OCRResultRecord:
        with self.uow_factory() as uow:
            current = uow.ocr.get(result_id)
            if current is None:
                raise OCRResultNotFound("OCR result not found")
            if current.created_by != actor.account_id and actor.role not in OCR_REVIEW_ROLES:
                raise PermissionDenied("Permission denied")
            result = uow.ocr.update_text(result_id, text)
            uow.commit()
            return result

    @staticmethod
    def _task(actor: AccountActor, source_type: str, filename: str | None, media_type: str, result: OCRResultRecord) -> TaskRecord:
        now = utc_now()
        succeeded = result.status == "completed"
        status = "succeeded" if succeeded else "failed"
        logs = (
            TaskLog("pending", now.isoformat()),
            TaskLog("running", now.isoformat()),
            TaskLog(status, now.isoformat(), "OCR completed by configured adapter" if succeeded else "OCR adapter did not produce text"),
        )
        return TaskRecord(
            f"ocr_{source_type}_{uuid4()}", f"ocr_{source_type}", status,
            1.0 if succeeded else 0.0,
            TaskPayload.from_mapping({"filename": filename, "media_type": media_type}),
            TaskPayload.from_mapping({"result_id": result.result_id}), result.result_id,
            result.error_code, result.error_message, actor.account_id, 1,
            logs, now, now, now, now,
        )
