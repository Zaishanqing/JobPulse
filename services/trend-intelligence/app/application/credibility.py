from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.domain.credibility import ranking_metrics
from app.ports.credibility import CredibilityStore
from app.ports.evaluation import EvaluationDatasetStore


class CredibilityService:
    def __init__(self, store: CredibilityStore, evaluation_store: EvaluationDatasetStore) -> None:
        self.store = store
        self.evaluation_store = evaluation_store

    def create_configuration(self, value: dict[str, object]) -> dict[str, object]:
        return self.store.create_configuration(value)

    def create_backtest(self, request: dict[str, object]) -> dict[str, object]:
        dataset_result = self.evaluation_store.published_ground_truth(str(request["dataset_id"]))
        if dataset_result is None:
            raise ValueError("backtest requires a published evaluation dataset")
        dataset, labels = dataset_result
        if dataset["version"] != request["dataset_version"]:
            raise ValueError("evaluation dataset version does not match")
        versions = dict(request.get("config_versions") or self.store.active_versions())
        self.store.payloads(versions)
        configs = self.store.payloads(versions)
        evaluated = [self._evaluate_slice(value, configs, int(request["k"]), labels)
                     for value in request["time_slices"]]
        results = [item for item in evaluated if item is not None]
        if not results:
            raise ValueError("backtest has no validated time slices")
        run, created = self.store.create_backtest(request, versions)
        if not created:
            return run
        self.store.save_backtest_results(str(run["id"]), results)
        return self.store.get_backtest(str(run["id"])) or run

    def backtest_metrics(self, run_id: str) -> dict[str, object] | None:
        slices = self.store.backtest_results(run_id)
        if slices is None:
            return None
        names = sorted({name for item in slices for name in item["metrics"]})
        aggregate = {
            name: round(sum(float(item["metrics"].get(name, 0)) for item in slices) / len(slices), 6)
            for name in names
        } if slices else {}
        window_stability = []
        for left, right in zip(slices, slices[1:]):
            left_keys = {item["candidate_key"] for item in left["predictions"][:10]}
            right_keys = {item["candidate_key"] for item in right["predictions"][:10]}
            union = left_keys | right_keys
            window_stability.append({
                "left_slice": left["slice_key"], "right_slice": right["slice_key"],
                "top_k_jaccard": round(len(left_keys & right_keys) / len(union), 6) if union else 1.0,
            })
        return {"aggregate": aggregate, "window_stability": window_stability, "slices": slices}

    def _evaluate_slice(self, value: dict[str, object], configs: dict[str, dict], k: int,
                        labels: list[dict[str, object]]) -> dict[str, object] | None:
        cutoff = datetime.fromisoformat(str(value["observation_cutoff"]).replace("Z", "+00:00"))
        validation_end = datetime.fromisoformat(str(value["validation_end"]).replace("Z", "+00:00"))
        if validation_end <= cutoff:
            raise ValueError("validation_end must be after observation_cutoff")
        terms = self.store.eligible_snapshot_terms(cutoff)
        truth = [item for item in labels if self._as_datetime(item["prediction_cutoff"]) == cutoff
                 and self._as_datetime(item["label_window_end"]) <= validation_end]
        if not truth:
            return None
        quality_flags = self._leakage_checks(terms, truth, cutoff)
        weights = dict(value.get("weights") or {"policy": 1, "academic": 1, "funding": 1, "github": 1})
        predictions = self._rank(terms, configs["job_knowledge"], weights)
        metrics = ranking_metrics(predictions, truth, k=k)
        persisted_truth = [{
            "candidate_key": item["candidate_key"], "entity_id": item["entity_id"],
            "entity_type": item["entity_type"], "direction": item["direction"],
            "observed_value": item.get("observed_value"),
            "confidence_level": item.get("confidence_level"),
            "prediction_cutoff": self._as_datetime(item["prediction_cutoff"]).isoformat(),
            "label_window_start": self._as_datetime(item["label_window_start"]).isoformat(),
            "label_window_end": self._as_datetime(item["label_window_end"]).isoformat(),
        } for item in truth]
        sources = sorted({str(item["source"]) for item in terms})
        ablation = {
            source: ranking_metrics(
                self._rank([item for item in terms if item["source"] != source], configs["job_knowledge"], weights),
                truth, k=k,
            )
            for source in sources
        }
        variants = list(value.get("weight_variants") or [])
        baseline = {item["candidate_key"] for item in predictions[:k]}
        stability = {}
        for index, variant in enumerate(variants):
            ranked = self._rank(terms, configs["job_knowledge"], dict(variant))
            candidate_set = {item["candidate_key"] for item in ranked[:k]}
            union = baseline | candidate_set
            stability[f"weights_{index + 1}"] = round(len(baseline & candidate_set) / len(union), 6) if union else 1.0
        return {
            "slice_key": str(value["slice_key"]), "observation_cutoff": cutoff,
            "validation_end": validation_end, "predictions": predictions,
            "ground_truth": persisted_truth, "metrics": metrics, "ablation_results": ablation,
            "stability_results": stability,
            "quality_flags": list(dict.fromkeys([
                *quality_flags, *(("low_historical_sample",) if len(terms) < 10 else ()),
            ])),
        }

    @staticmethod
    def _as_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _leakage_checks(self, terms: list[dict[str, object]], truth: list[dict[str, object]],
                        cutoff: datetime) -> list[str]:
        flags: list[str] = []
        for label in truth:
            evidence = [*(label.get("sample_evidence") or []), *(label.get("label_evidence") or [])]
            for item in evidence:
                observed_at = item.get("observed_at")
                if not observed_at:
                    flags.append("missing_evidence_timestamp")
                    continue
                if self._as_datetime(observed_at) <= cutoff:
                    raise ValueError("label evidence at or before prediction cutoff would leak into evaluation")
        return flags

    @staticmethod
    def _rank(terms: list[dict[str, object]], knowledge: dict, weights: dict[str, float]) -> list[dict[str, object]]:
        source_terms: dict[str, list[str]] = defaultdict(list)
        for item in terms:
            source_terms[str(item["source"])].append(str(item["term"]).casefold())
        results = []
        for domain, specification in knowledge.items():
            keywords = [str(item).casefold() for item in specification.get("research_keywords", [])]
            contributions = {
                source: sum(any(keyword in term or term in keyword for keyword in keywords) for term in values)
                        * float(weights.get(source if source not in {"arxiv", "cvf", "acl"} else "academic", 1.0))
                for source, values in source_terms.items()
            }
            signal = sum(contributions.values())
            for role in specification.get("jobs", []):
                results.append({
                    "candidate_key": str(role["name"]), "industry_domain": domain,
                    "score": round(signal, 6), "direction": "rising" if signal > 0 else "stable",
                    "source_contributions": contributions,
                })
        return sorted(results, key=lambda item: (-float(item["score"]), str(item["candidate_key"])))
