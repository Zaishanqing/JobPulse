from __future__ import annotations

import signal
import time

from app.application import BuildGraphUseCase, BuildJobUseCase
from app.application.build_job_runner import BuildJobRunner
from app.config import Settings
from app.database import create_database
from app.infrastructure.sqlalchemy import SqlAlchemyUnitOfWork


def main() -> None:
    settings = Settings.from_env()
    database = create_database(settings)
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def uow_factory():
        return SqlAlchemyUnitOfWork(database.session_factory)

    jobs = BuildJobUseCase(uow_factory)
    runner = BuildJobRunner(
        jobs, BuildGraphUseCase(uow_factory), settings.build_job_worker_id
    )
    try:
        while not stopping:
            if runner.run_once() is None:
                time.sleep(settings.build_job_poll_seconds)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
