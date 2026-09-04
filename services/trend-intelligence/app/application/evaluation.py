from __future__ import annotations

from app.ports.evaluation import EvaluationDatasetStore


class EvaluationDatasetService:
    def __init__(self, store: EvaluationDatasetStore) -> None:
        self.store = store

    def generate_samples(self, dataset_id: str, source_type: str, records: list[dict[str, object]], actor: str):
        if source_type not in {"published_position_graph", "historical_hiring", "manual_import"}:
            raise ValueError("unsupported evaluation sample source")
        return self.store.add_samples(dataset_id, source_type, records, actor)

    def create(self, value: dict[str, object]):
        return self.store.create_dataset(value)

    def revise(self, dataset_id: str, version: str, actor: str):
        return self.store.revise_dataset(dataset_id, version, actor)

    def submit_label(self, sample_id: str, value: dict[str, object]):
        return self.store.submit_label(sample_id, value)

    def review_label(self, label_id: str, decision: str, reviewer: str, note: str | None):
        return self.store.review_label(label_id, decision, reviewer, note)
