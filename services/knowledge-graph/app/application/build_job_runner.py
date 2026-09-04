from __future__ import annotations

from app.application.use_cases import BuildGraphUseCase, BuildJobUseCase
from app.domain.build_jobs import BuildJobRecord


class BuildJobRunner:
    def __init__(
        self,
        jobs: BuildJobUseCase,
        builds: BuildGraphUseCase,
        worker_id: str,
    ) -> None:
        self.jobs = jobs
        self.builds = builds
        self.worker_id = worker_id

    def run_once(self, job_id: int | None = None) -> BuildJobRecord | None:
        job = self.jobs.claim(self.worker_id, job_id)
        if job is None:
            return None
        try:
            result = self.builds.execute(self.jobs.command(job))
        except Exception as exc:
            self.jobs.fail(job.job_id, exc)
            return self.jobs.get(job.job_id)
        return self.jobs.succeed(job.job_id, result.build_run_id)
