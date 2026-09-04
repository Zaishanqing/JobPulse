"""Standalone worker for claimed Data Validation tasks."""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from enum import Enum
from threading import Event

import app.models  # noqa: F401
from app.contexts.data_validation.application import (
    ExecuteValidationTaskUseCase,
    ValidateBundleUseCase,
    ValidationTaskExecutionStatus,
)
from app.contexts.data_validation.validators import default_validator_set
from app.core.config import Settings, settings
from app.core.database import Database, create_database
from app.infrastructure.data_validation import (
    DataValidationPersistenceConflict,
    SqlAlchemyDataValidationUnitOfWork,
    SqlAlchemyValidationInputReader,
    SqlAlchemyValidationPortFactory,
)


LOGGER = logging.getLogger(__name__)


class ValidationWorkerResult(str, Enum):
    DISABLED = "disabled"
    NO_WORK = "no_work"
    RETRYABLE_CONFLICT = "retryable_conflict"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ValidationWorker:
    def __init__(
        self,
        *,
        mode: str,
        uow_factory,
        executor: ExecuteValidationTaskUseCase,
    ) -> None:
        if mode not in {"off", "observe", "enforce"}:
            raise ValueError("Unsupported DATA_VALIDATION_MODE")
        self._mode = mode
        self._uow_factory = uow_factory
        self._executor = executor

    def run_once(self) -> ValidationWorkerResult:
        if self._mode == "off":
            return ValidationWorkerResult.DISABLED
        try:
            with self._uow_factory() as uow:
                task = uow.tasks.claim_next_pending()
                if task is None:
                    return ValidationWorkerResult.NO_WORK
                uow.commit()
        except DataValidationPersistenceConflict:
            return ValidationWorkerResult.RETRYABLE_CONFLICT
        result = self._executor.execute(task)
        if (
            result.status is ValidationTaskExecutionStatus.FAILED
            or result.error_code is not None
        ):
            return ValidationWorkerResult.FAILED
        return ValidationWorkerResult.SUCCEEDED


@dataclass
class ValidationWorkerRuntime:
    database: Database
    worker: ValidationWorker

    def close(self) -> None:
        self.database.dispose()


def build_worker_runtime(
    runtime_settings: Settings = settings,
) -> ValidationWorkerRuntime:
    database = create_database(runtime_settings.DATABASE_URL)

    def uow_factory():
        return SqlAlchemyDataValidationUnitOfWork(database.session_factory)

    executor = ExecuteValidationTaskUseCase(
        uow_factory=uow_factory,
        input_reader=SqlAlchemyValidationInputReader(database.session_factory),
        port_factory=SqlAlchemyValidationPortFactory(database.session_factory),
        validator=ValidateBundleUseCase(default_validator_set()),
    )
    return ValidationWorkerRuntime(
        database,
        ValidationWorker(
            mode=runtime_settings.DATA_VALIDATION_MODE,
            uow_factory=uow_factory,
            executor=executor,
        ),
    )


def run_worker(
    runtime: ValidationWorkerRuntime,
    *,
    stop: Event | None = None,
    poll_seconds: float = settings.DATA_VALIDATION_WORKER_POLL_SECONDS,
) -> None:
    if poll_seconds <= 0:
        raise ValueError(
            "DATA_VALIDATION_WORKER_POLL_SECONDS must be greater than zero"
        )
    stopped = stop or Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("validation_worker_stopping", extra={"signal": signum})
        stopped.set()

    handlers = {signal.SIGINT: signal.getsignal(signal.SIGINT)}
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        handlers[sigterm] = signal.getsignal(sigterm)
    try:
        signal.signal(signal.SIGINT, request_stop)
        if sigterm is not None:
            signal.signal(sigterm, request_stop)
        LOGGER.info("validation_worker_started")
        while not stopped.is_set():
            try:
                result = runtime.worker.run_once()
            except Exception:  # noqa: BLE001 - database/network outages are retryable
                LOGGER.exception("validation_worker_transient_error")
                stopped.wait(poll_seconds)
                continue
            if result in {
                ValidationWorkerResult.DISABLED,
                ValidationWorkerResult.NO_WORK,
                ValidationWorkerResult.RETRYABLE_CONFLICT,
            }:
                stopped.wait(poll_seconds)
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL)
    runtime = build_worker_runtime()
    try:
        run_worker(
            runtime,
            poll_seconds=settings.DATA_VALIDATION_WORKER_POLL_SECONDS,
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
