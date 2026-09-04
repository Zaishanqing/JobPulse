from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from pathlib import Path
from threading import Lock

from jobgraph_contracts.cv_extraction_http import (
    CVExecutionMetadata,
    CVExtractionResponseV2,
    CVExtractionResponseV3,
)
from jobgraph_contracts.position_classifier import PositionClassifier

from src.evidence import inject_evidence_document_id, validate_evidence_alignment
from src.checkpoints import SQLiteExtractionCheckpointStore, build_checkpoint_key
from src.deepseek_client import DeepSeekTimeoutError
from src.pipeline import CVExtractionPipeline
from src.preprocess import build_source_blocks, normalize_cv_text
from src.skill_taxonomy import build_taxonomy_projection

from .config import Settings


LOGGER = logging.getLogger(__name__)
VALIDATION_POLICY_VERSION = "cv-validation-policy.v3"


@dataclass(frozen=True)
class CVExtractionDocument:
    document_id: str
    raw_text: str


class CVExtractionApplicationService:
    def __init__(
        self,
        settings: Settings,
        pipeline_factory: Callable[[], CVExtractionPipeline] | None = None,
        position_classifier: PositionClassifier | None = None,
    ) -> None:
        self._settings = settings
        self._checkpoint_store = (
            SQLiteExtractionCheckpointStore(settings.CV_EXTRACTION_CHECKPOINT_PATH)
            if settings.CV_EXTRACTION_CHECKPOINT_PATH
            else None
        )
        self._pipeline_factory = pipeline_factory or self._build_pipeline
        # Do not create the provider client during process startup. The
        # classifier constructs a DeepSeek client eagerly, while the provider
        # credential is an optional runtime capability for health/readiness.
        # Instantiate it on the first v3 extraction instead, so the service
        # can boot without an API key and report the missing provider
        # credential at request time.
        self._position_classifier = position_classifier
        self._progress_lock = Lock()
        self._progress: dict[str, dict] = {}

    def progress_for(self, document_id: str) -> dict:
        with self._progress_lock:
            return deepcopy(
                self._progress.get(
                    document_id,
                    {"stage": "queued", "percent": 0.0},
                )
            )

    def _publish_progress(self, document_id: str, progress: dict) -> None:
        value = {
            **progress,
            "document_id": document_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._progress_lock:
            self._progress[document_id] = value

    def _get_position_classifier(self) -> PositionClassifier:
        if self._position_classifier is None:
            self._position_classifier = PositionClassifier(
                catalog_path=self._settings.POSITION_TAXONOMY_PATH,
                model=self._settings.MODEL,
                max_attempts=self._settings.POSITION_CLASSIFICATION_MAX_ATTEMPTS,
                timeout_seconds=self._settings.CV_EXTRACTION_API_TIMEOUT_SECONDS,
                transport_attempts=1,
                json_attempts=1,
            )
        return self._position_classifier

    def extract_v2(self, document: CVExtractionDocument) -> dict:
        self._publish_progress(document.document_id, {"stage": "preparing", "percent": 0.12})
        checkpoint_key = self._checkpoint_key(document)
        cached = self._load_checkpoint(checkpoint_key, "v2")
        if cached is not None:
            LOGGER.info(
                "cv_extraction_checkpoint_hit checkpoint=%s stage=v2",
                checkpoint_key[:12],
            )
            self._publish_progress(
                document.document_id,
                {"stage": "position_classifying", "percent": 0.9, "checkpoint_hit": True},
            )
            return CVExtractionResponseV2.model_validate(cached).model_dump(mode="json")
        started = perf_counter()
        extraction_started_at = datetime.now(timezone.utc).isoformat()
        LOGGER.info(
            "cv_extraction_stage_started checkpoint=%s document_id=%s stage=v2",
            checkpoint_key[:12] if checkpoint_key else "disabled",
            document.document_id,
        )
        pipeline = self._pipeline_factory()
        extraction_started = perf_counter()
        result = pipeline.extract_one(
            document_id=document.document_id,
            raw_text=document.raw_text,
            progress_callback=lambda value: self._publish_progress(
                document.document_id, value
            ),
        )
        extraction_duration_ms = int(round((perf_counter() - extraction_started) * 1000))
        validation_started_at = datetime.now(timezone.utc).isoformat()
        validation_started = perf_counter()
        self._publish_progress(
            document.document_id,
            {"stage": "contract_validating", "percent": 0.82},
        )
        extraction = inject_evidence_document_id(
            result.extraction.model_dump(mode="json"), document.document_id
        )
        validate_evidence_alignment(
            extraction,
            document_id=document.document_id,
            raw_text=document.raw_text,
            source_blocks=build_source_blocks(
                normalize_cv_text(document.raw_text)
            ),
        )
        normalized = result.normalized.model_dump(mode="json")
        skill_ids = (
            item.skill_id
            for item in result.normalized.normalized_skills
            if item.resolution_status == "resolved" and item.skill_id is not None
        )
        taxonomy_version = self._taxonomy_version()
        validation_duration_ms = int(round((perf_counter() - validation_started) * 1000))
        api_attempts = tuple(getattr(result, "api_attempts", ()))
        extraction_attempts = sum(
            max(1, int(item.get("transport_attempt_count") or 0))
            for item in api_attempts
        ) or 1
        stages = [
            {
                "stage": "extracting",
                "started_at": extraction_started_at,
                "duration_ms": extraction_duration_ms,
                "provider": self._settings.PROVIDER,
                "model": self._settings.MODEL,
                "attempt_count": extraction_attempts,
                "metadata": {"api_attempts": list(api_attempts)},
            },
            {
                "stage": "contract_validating",
                "started_at": validation_started_at,
                "duration_ms": validation_duration_ms,
                "attempt_count": 1,
                "metadata": {},
            },
        ]
        semantic_repair_count = int(getattr(result, "semantic_repair_count", 0))
        if semantic_repair_count:
            stages.append(
                {
                    "stage": "semantic_repairing",
                    "started_at": extraction_started_at,
                    "duration_ms": sum(
                        int(item.get("elapsed_ms") or 0)
                        for item in api_attempts
                        if item.get("mode") != "initial"
                    ),
                    "provider": self._settings.PROVIDER,
                    "model": self._settings.MODEL,
                    "attempt_count": semantic_repair_count,
                    "metadata": {},
                }
            )
        response = CVExtractionResponseV2(
            document_id=document.document_id,
            execution=CVExecutionMetadata(
                mode="llm",
                provider=self._settings.PROVIDER,
                model=self._settings.MODEL,
                prompt_version=self._settings.PROMPT_VERSION,
                schema_version=self._settings.SCHEMA_VERSION,
                normalization_version=self._settings.NORMALIZATION_VERSION,
                taxonomy_version=taxonomy_version,
                latency_ms=int(round((perf_counter() - started) * 1000)),
                is_demo=False,
                stages=stages,
            ),
            extraction_result=extraction,
            normalized_result=normalized,
            review_flags=list(result.review_flags),
            skill_taxonomy=build_taxonomy_projection(
                skill_ids,
                pipeline.skill_taxonomy,
            ),
        )
        payload = response.model_dump(mode="json")
        self._save_checkpoint(checkpoint_key, "v2", payload)
        self._publish_progress(
            document.document_id,
            {"stage": "position_classifying", "percent": 0.9},
        )
        LOGGER.info(
            "cv_extraction_stage_completed checkpoint=%s document_id=%s stage=v2 duration_ms=%s",
            checkpoint_key[:12] if checkpoint_key else "disabled",
            document.document_id,
            payload["execution"]["latency_ms"],
        )
        return payload

    def extract_v3(self, document: CVExtractionDocument) -> dict:
        checkpoint_key = self._checkpoint_key(document)
        cached = self._load_checkpoint(checkpoint_key, "v3")
        if cached is not None:
            LOGGER.info(
                "cv_extraction_checkpoint_hit checkpoint=%s stage=v3",
                checkpoint_key[:12],
            )
            self._publish_progress(
                document.document_id,
                {"stage": "completed", "percent": 1.0, "checkpoint_hit": True},
            )
            return CVExtractionResponseV3.model_validate(cached).model_dump(mode="json")
        started = perf_counter()
        LOGGER.info(
            "cv_extraction_stage_started checkpoint=%s document_id=%s stage=v3",
            checkpoint_key[:12] if checkpoint_key else "disabled",
            document.document_id,
        )
        payload = self.extract_v2(document)
        # 岗位分类是又一次 LLM 调用；超预算时快速失败，主后端队列重试会命中
        # v2/section checkpoint，秒过前序阶段后在此继续。
        budget_seconds = self._settings.CV_EXTRACTION_TASK_BUDGET_SECONDS
        budget_elapsed = perf_counter() - started
        if budget_elapsed > budget_seconds:
            raise DeepSeekTimeoutError(
                "CV extraction task budget exhausted before position classification "
                f"(elapsed={budget_elapsed:.1f}s, budget={budget_seconds}s)"
            )
        profiles = self._position_profiles(payload)
        position_classifier = self._get_position_classifier()
        classification_started_at = datetime.now(timezone.utc).isoformat()
        classification_started = perf_counter()
        self._publish_progress(
            document.document_id,
            {"stage": "position_classifying", "percent": 0.9},
        )
        classify_with_metadata = getattr(
            position_classifier, "classify_with_metadata", None
        )
        if callable(classify_with_metadata):
            decisions, classification_metadata = classify_with_metadata(profiles)
        else:
            # Older deterministic classifiers implement only classify(). They
            # make no provider call, so the stage must report zero LLM attempts.
            decisions = position_classifier.classify(profiles)
            classification_metadata = {
                "profile_count": len(profiles),
                "deterministic_count": len(profiles),
                "llm_profile_count": 0,
                "attempt_count": 0,
            }
        classifications = []
        for profile in profiles:
            decision = decisions[profile["document_id"]]
            classifications.append(
                {
                    "feature_id": profile["feature_id"],
                    "source_object_id": profile["source_object_id"],
                    "source_scope": profile["source_scope"],
                    "role_kind": profile["role_kind"],
                    "job_classification": position_classifier.materialize(
                        decision,
                        source_title=profile["title"],
                    ),
                }
            )
        payload["contract_version"] = "cv-extraction-http.v3"
        payload["normalized_result"]["position_classifications"] = classifications
        payload["execution"].setdefault("stages", []).append(
            {
                "stage": "position_classifying",
                "started_at": classification_started_at,
                "duration_ms": int(
                    round((perf_counter() - classification_started) * 1000)
                ),
                "provider": (
                    self._settings.PROVIDER
                    if classification_metadata["llm_profile_count"]
                    else None
                ),
                "model": (
                    self._settings.MODEL
                    if classification_metadata["llm_profile_count"]
                    else None
                ),
                "attempt_count": classification_metadata["attempt_count"],
                "metadata": classification_metadata,
            }
        )
        payload["execution"]["latency_ms"] = int(
            round((perf_counter() - started) * 1000)
        )
        response = CVExtractionResponseV3.model_validate(payload).model_dump(mode="json")
        self._save_checkpoint(checkpoint_key, "v3", response)
        self._publish_progress(
            document.document_id,
            {"stage": "completed", "percent": 1.0},
        )
        LOGGER.info(
            "cv_extraction_stage_completed checkpoint=%s document_id=%s stage=v3 duration_ms=%s",
            checkpoint_key[:12] if checkpoint_key else "disabled",
            document.document_id,
            response["execution"]["latency_ms"],
        )
        return response

    def _checkpoint_key(self, document: CVExtractionDocument) -> str | None:
        if self._checkpoint_store is None:
            return None
        return build_checkpoint_key(
            document_id=document.document_id,
            raw_text=document.raw_text,
            runtime_fingerprint=self._checkpoint_fingerprint(),
        )

    def _checkpoint_fingerprint(self) -> dict[str, str]:
        return {
            "provider": self._settings.PROVIDER,
            "model": self._settings.MODEL,
            "prompt_version": self._settings.PROMPT_VERSION,
            "schema_version": self._settings.SCHEMA_VERSION,
            "normalization_version": self._settings.NORMALIZATION_VERSION,
            "taxonomy_version": self._taxonomy_version(),
            "validation_policy_version": VALIDATION_POLICY_VERSION,
        }

    def _load_checkpoint(
        self,
        checkpoint_key: str | None,
        stage: str,
    ) -> dict | None:
        if self._checkpoint_store is None or checkpoint_key is None:
            return None
        return self._checkpoint_store.load(checkpoint_key, stage)

    def _save_checkpoint(
        self,
        checkpoint_key: str | None,
        stage: str,
        payload: dict,
    ) -> None:
        if self._checkpoint_store is None or checkpoint_key is None:
            return
        self._checkpoint_store.save(checkpoint_key, stage, payload)

    def _position_profiles(self, payload: dict) -> list[dict]:
        document_id = str(payload["document_id"])
        extraction = payload["extraction_result"]
        normalized = payload["normalized_result"]
        taxonomy = payload["skill_taxonomy"]
        skills = [
            item.get("canonical_name")
            for item in normalized.get("normalized_skills", [])
            if isinstance(item, dict) and item.get("canonical_name")
        ][:24]
        domains = []
        for skill in taxonomy.get("skills", []):
            for relation in skill.get("classifications", []):
                if (
                    relation.get("facet") == "domain"
                    and isinstance(relation.get("code"), str)
                    and relation["code"] not in domains
                ):
                    domains.append(relation["code"])
        profiles = []
        personal = extraction.get("personal_info")
        if isinstance(personal, dict):
            title = personal.get("expected_position")
            if isinstance(title, str) and title.strip():
                profiles.append(
                    {
                        "document_id": (
                            f"{document_id}:personal_info:expected_position"
                        ),
                        "feature_id": "role_personal_info_expected_position",
                        "source_object_id": "personal_info",
                        "source_scope": "personal_info.expected_position",
                        "role_kind": "expected",
                        "title": title.strip(),
                        "responsibilities": [],
                        "skills": skills,
                        "skill_domains": domains[:8],
                        "available_evidence_refs": self._field_evidence_refs(
                            personal,
                            "expected_position",
                        ),
                    }
                )
        for entry in extraction.get("work_experience", []):
            if not isinstance(entry, dict):
                continue
            title = entry.get("position")
            entry_id = entry.get("entry_id")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(entry_id, str)
                or not entry_id
            ):
                continue
            responsibilities = []
            for fact in [
                *entry.get("responsibilities", []),
                *entry.get("achievements", []),
            ]:
                if not isinstance(fact, dict):
                    continue
                text = fact.get("value") or fact.get("text")
                evidence = fact.get("evidence")
                evidence_ref = (
                    evidence.get("source_id")
                    if isinstance(evidence, dict)
                    else None
                )
                if isinstance(text, str) and text.strip():
                    responsibilities.append(
                        {
                            "text": text.strip(),
                            "evidence_ref": (
                                str(evidence_ref)
                                if evidence_ref is not None
                                else None
                            ),
                        }
                    )
            evidence_refs = [
                item["evidence_ref"]
                for item in responsibilities
                if item["evidence_ref"] is not None
            ]
            for value in self._field_evidence_refs(entry, "position"):
                if value not in evidence_refs:
                    evidence_refs.append(value)
            profiles.append(
                {
                    "document_id": f"{document_id}:{entry_id}:position",
                    "feature_id": f"role_{entry_id}_position",
                    "source_object_id": entry_id,
                    "source_scope": "work_experience.position",
                    "role_kind": "historical",
                    "title": title.strip(),
                    "responsibilities": responsibilities[:8],
                    "skills": skills,
                    "skill_domains": domains[:8],
                    "available_evidence_refs": evidence_refs,
                }
            )
        return profiles

    @staticmethod
    def _field_evidence_refs(item: dict, field_name: str) -> list[str]:
        refs = []
        for binding in item.get("field_evidence", []):
            if (
                isinstance(binding, dict)
                and binding.get("field_name") == field_name
                and isinstance(binding.get("evidence"), dict)
                and binding["evidence"].get("source_id")
            ):
                value = str(binding["evidence"]["source_id"])
                if value not in refs:
                    refs.append(value)
        return refs

    def _taxonomy_version(self) -> str:
        manifest_path = Path(self._settings.RESOURCE_MANIFEST_PATH)
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return str(manifest["taxonomy_version"])

    def _build_pipeline(self) -> CVExtractionPipeline:
        return CVExtractionPipeline(
            model=self._settings.MODEL,
            normalization_path=self._settings.NORMALIZATION_PATH,
            skill_taxonomy_path=self._settings.SKILL_TAXONOMY_PATH,
            continue_on_error=False,
            max_workers=self._settings.CV_EXTRACTION_MAX_WORKERS,
            semantic_retry_attempts=(
                self._settings.CV_EXTRACTION_SEMANTIC_RETRY_ATTEMPTS
            ),
            api_timeout_seconds=self._settings.CV_EXTRACTION_API_TIMEOUT_SECONDS,
            task_budget_seconds=self._settings.CV_EXTRACTION_TASK_BUDGET_SECONDS,
            parallel_section_extraction=(
                self._settings.CV_EXTRACTION_PARALLEL_SECTION_EXTRACTION
            ),
            checkpoint_store=self._checkpoint_store,
            checkpoint_fingerprint=self._checkpoint_fingerprint(),
        )
