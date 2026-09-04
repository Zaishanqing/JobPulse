from collections import Counter
from multi_company_scraper.models.job_data import JobData


class JobCollector:
    def __init__(self):
        self._jobs: list[JobData] = []

    def add(self, job: JobData):
        self._jobs.append(job)

    def add_batch(self, jobs: list[JobData]):
        self._jobs.extend(jobs)

    def get_all(self) -> list[JobData]:
        return list(self._jobs)

    def total(self) -> int:
        return len(self._jobs)

    def clear(self):
        self._jobs.clear()

    def stats(self) -> dict:
        company_counter = Counter(j.company_name for j in self._jobs)
        city_counter = Counter(j.city for j in self._jobs if j.city)
        platform_counter = Counter(j.source_platform for j in self._jobs)
        return {
            "total_jobs": len(self._jobs),
            "companies": dict(company_counter.most_common()),
            "cities": dict(city_counter.most_common()),
            "platforms": dict(platform_counter.most_common()),
        }
