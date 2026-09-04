#!/usr/bin/env python3
"""Import an audited JD package through the main system with bounded concurrency.

This command only performs the authoritative main-system import, confirmation,
validation gate, and publication. Published facts are delivered to the
knowledge graph by the existing Outbox worker; the importer does not race that
worker by calling the synchronous KG endpoint for every JD.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import time
from collections import Counter
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

from app.core.config import settings
from app.core.database import create_database

from run_audit_jd_kg_prediction_flow_v2 import (
    API,
    FlowError,
    PreparedRecord,
    RecordOutcome,
    build_group_summary,
    convert_extraction,
    convert_normalization,
    ensure_main_catalog_skills,
    ensure_taxonomy_positions,
    locate_batch_root,
    login_main,
    prepare_records,
    project_tempdir,
    validate_structural_contracts,
    validate_with_repository_contracts,
    write_report,
)

IMPORT_LOCK_KEY = int.from_bytes(b"JDIMPORT", byteorder="big", signed=False)
RESULT_COUNT_MEANINGS = {
    "published_this_run": "由本次命令完成发布",
    "skipped_already_published": "处理时已经发布，本次跳过",
    "awaiting_classification_review": "等待岗位分类审核",
    "awaiting_normalization_review": "等待归一化审核",
    "failed": "导入失败",
}


@contextmanager
def exclusive_import_process():
    database = create_database(settings.DATABASE_URL)
    connection = database.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": IMPORT_LOCK_KEY},
            ).scalar_one()
        )
        if not acquired:
            raise FlowError(
                "Another concurrent JD import is already running; do not start a second process."
            )
        yield database
    finally:
        if "acquired" in locals() and acquired:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": IMPORT_LOCK_KEY},
            )
        connection.close()
        database.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import audited JD data concurrently and let Outbox deliver published facts to KG."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--main-url", default="http://127.0.0.1:8000")
    parser.add_argument("--main-username", default="demo_admin")
    parser.add_argument("--main-password", default="password123")
    parser.add_argument(
        "--batch-name",
        help="Stable source_name prefix; defaults to the clean package root name.",
    )
    parser.add_argument("--workers", type=int, default=256)
    parser.add_argument("--http-connections", type=int, default=96)
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=0,
        help="maximum queued/running records; 0 uses twice --workers",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--validation-timeout",
        type=float,
        default=0.0,
        help="seconds to wait for Validation; 0 waits until the worker reaches a terminal state",
    )
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--only-family", action="append", default=[])
    parser.add_argument("--skip-source-document", action="append", default=[])
    parser.add_argument(
        "--only-source-document-file",
        type=Path,
        help="UTF-8 text file containing one Extraction document_id per line",
    )
    parser.add_argument(
        "--source-metadata-file",
        type=Path,
        help=(
            "UTF-8 CSV mapping document_id to crawl_date. When supplied, every "
            "selected record must have a valid crawl_date and it is persisted as "
            "the JD publish_date so graph source windows retain Bundle time."
        ),
    )
    parser.add_argument(
        "--source-selection-file",
        type=Path,
        help=(
            "JSON selection manifest whose records carry document_id and "
            "crawl_date. It supplies both the exact record filter and the "
            "source-carried date used as JD publish_date."
        ),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("data/.audit-flow-tmp"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/extraction-audit/concurrent-jd-import-report.json"),
    )
    parser.add_argument(
        "--approve-validation-warnings",
        action="store_true",
        help="approve only the Validation WARN task whose JD lineage exactly matches the current record",
    )
    parser.add_argument(
        "--retry-passes",
        type=int,
        default=2,
        help="additional whole-record passes for transient transport/database failures",
    )
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args()


def validation_review_task(database, extraction_task_id: str) -> tuple[str, str]:
    statement = text(
        """
        SELECT review_tasks.id, review_tasks.status
        FROM review_tasks
        JOIN validation_reports
          ON validation_reports.id = review_tasks.object_id
        JOIN data_validation_tasks
          ON data_validation_tasks.id = validation_reports.data_validation_task_id
        WHERE review_tasks.object_type = 'data_validation_report'
          AND data_validation_tasks.extraction_task_id = :extraction_task_id
        ORDER BY review_tasks.created_at DESC
        """
    )
    with database.session_factory() as session:
        tasks = list(
            session.execute(
                statement,
                {"extraction_task_id": extraction_task_id},
            ).tuples()
        )
    if len(tasks) != 1:
        raise FlowError(
            "Expected one validation review for extraction task "
            f"{extraction_task_id}, found {len(tasks)}"
        )
    task_id, status = tasks[0]
    return str(task_id), str(status)


def wait_for_validation(
    database,
    *,
    jd_id: str,
    timeout_seconds: float,
) -> tuple[str, str]:
    statement = text(
        """
        SELECT data_validation_tasks.extraction_task_id,
               data_validation_tasks.status,
               data_validation_tasks.last_error_code,
               data_validation_tasks.last_error_message,
               validation_reports.conclusion
        FROM job_descriptions
        JOIN data_validation_tasks
          ON data_validation_tasks.extraction_task_id = job_descriptions.extraction_task_id
        LEFT JOIN validation_reports
          ON validation_reports.data_validation_task_id = data_validation_tasks.id
        WHERE job_descriptions.id = :jd_id
        ORDER BY data_validation_tasks.created_at DESC
        LIMIT 1
        """
    )
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    while True:
        with database.session_factory() as session:
            state = session.execute(statement, {"jd_id": jd_id}).mappings().first()
        if state is not None and state["status"] == "failed":
            raise FlowError(
                "Validation failed for JD "
                f"{jd_id}: {state['last_error_code']}: {state['last_error_message']}"
            )
        if state is not None and state["status"] == "succeeded":
            conclusion = state["conclusion"]
            if conclusion not in {"pass", "warn", "block"}:
                raise FlowError(f"Validation report is missing for JD {jd_id}")
            return str(state["extraction_task_id"]), str(conclusion)
        if deadline is not None and time.monotonic() >= deadline:
            raise FlowError(
                f"Validation did not complete within {timeout_seconds:g}s for JD {jd_id}"
            )
        time.sleep(1.0)


def publish_after_validation(
    *,
    api: API,
    token: str,
    jd_id: str,
    database,
    approve_validation_warnings: bool,
    timeout_seconds: float,
) -> None:
    path = f"/api/v1/jds/{jd_id}/parse-result/publish"
    extraction_task_id, conclusion = wait_for_validation(
        database,
        jd_id=jd_id,
        timeout_seconds=timeout_seconds,
    )
    if conclusion == "block":
        raise FlowError(f"Validation blocked JD {jd_id}")
    if conclusion == "warn" and approve_validation_warnings:
        task_id, review_status = validation_review_task(database, extraction_task_id)
        if review_status in {"pending", "claimed"}:
            api.request(
                "POST",
                f"/api/v1/review-tasks/{task_id}/approve",
                token=token,
                json={
                    "review_comment": (
                        "Approved while importing the post-reviewed audited JD batch."
                    )
                },
            )
        elif review_status != "approved":
            raise FlowError(
                f"Validation review {task_id} has non-publishable status {review_status}"
            )
    api.request("POST", path, token=token)


def import_record(
    *,
    api: API,
    token: str,
    batch_name: str,
    record: PreparedRecord,
    existing_by_source_name: dict[str, dict[str, object]],
    taxonomy_positions: dict[str, dict[str, object]],
    catalog_skills: dict[str, dict[str, object]],
    approve_validation_warnings: bool,
    validation_timeout: float,
    database,
    attempt_number: int,
    publish_date: str | None,
    source_platform: str | None,
) -> RecordOutcome:
    standard_position = (
        taxonomy_positions[record.position_code]
        if record.classification_resolved
        else None
    )
    outcome = RecordOutcome(
        source_document_id=record.source_document_id,
        position_code=record.position_code,
        family_code=record.family_code,
        main_position_id=(str(standard_position["position_id"]) if standard_position else None),
        title=record.title,
        raw_source=record.raw_source,
        attempt_count=attempt_number,
        warnings=list(record.warnings),
    )
    stage = "create_or_reuse_jd"
    try:
        source_name = ":".join(
            value
            for value in (batch_name, source_platform, record.source_document_id)
            if value
        )
        created = existing_by_source_name.get(source_name)
        if created is None:
            create_payload = {
                "source_type": "audited_real_extraction_replay",
                "source_name": source_name,
                "title": record.title,
                "raw_text": record.source_raw_text,
                "cleaned_text": record.cleaned_text,
            }
            if publish_date is not None:
                create_payload["publish_date"] = publish_date
            created = api.request(
                "POST",
                "/api/v1/jds/text",
                token=token,
                json=create_payload,
            )["data"]
        main_jd_id = str(created.get("jd_id") or created["id"])
        outcome.main_jd_id = main_jd_id
        stage = "load_parse_result"
        existing_parse = api.request(
            "GET",
            f"/api/v1/jds/{main_jd_id}/parse-result",
            token=token,
            expected=(200, 404),
        ).get("data")
        if existing_parse and existing_parse.get("workflow_status") == "published":
            outcome.status = "skipped_already_published"
            outcome.sync_status = "owned_by_outbox"
            return outcome

        stage = "convert_payload"
        extraction = convert_extraction(copy.deepcopy(record.extraction_source), main_jd_id)
        normalization = convert_normalization(
            record.normalization_source,
            record.extraction_source,
            main_jd_id,
            catalog_skills,
            standard_position_id=(str(standard_position["position_id"]) if standard_position else None),
            standard_position_code=(str(standard_position["position_code"]) if standard_position else None),
            standard_position_name=(str(standard_position["position_name"]) if standard_position else None),
            standard_family_code=(str(standard_position["family_code"]) if standard_position else None),
            standard_family_name=(str(standard_position["family_name"]) if standard_position else None),
            standard_skill_domain_codes=(standard_position["skill_domain_codes"] if standard_position else ()),
        )
        validate_structural_contracts(extraction, normalization, record.raw_text)
        validate_with_repository_contracts(extraction, normalization)

        if existing_parse and existing_parse.get("workflow_status") == "reviewed":
            if not record.publishable_projection:
                raise FlowError(
                    f"Reviewed JD {main_jd_id} no longer has a publishable projection"
                )
            _, conclusion = wait_for_validation(
                database,
                jd_id=main_jd_id,
                timeout_seconds=validation_timeout,
            )
            if conclusion != "block":
                stage = "publish_reviewed"
                publish_after_validation(
                    api=api,
                    token=token,
                    jd_id=main_jd_id,
                    database=database,
                    approve_validation_warnings=approve_validation_warnings,
                    timeout_seconds=validation_timeout,
                )
                outcome.status = "published_this_run"
                outcome.sync_status = "queued_by_outbox"
                return outcome
            if (
                existing_parse.get("extraction_result") == extraction
                and existing_parse.get("normalized_result") == normalization
            ):
                raise FlowError(f"Validation blocked unchanged JD {main_jd_id}")

        stage = "write_parse_result"
        api.request(
            "PUT",
            f"/api/v1/jds/{main_jd_id}/parse-result",
            token=token,
            json={"extraction_result": extraction, "normalized_result": normalization},
        )
        if not record.publishable_projection:
            outcome.status = (
                "awaiting_normalization_review"
                if record.classification_resolved
                else "awaiting_classification_review"
            )
            return outcome

        stage = "confirm_parse_result"
        api.request(
            "POST",
            f"/api/v1/jds/{main_jd_id}/parse-result/confirm",
            token=token,
        )
        stage = "publish_after_validation"
        publish_after_validation(
            api=api,
            token=token,
            jd_id=main_jd_id,
            database=database,
            approve_validation_warnings=approve_validation_warnings,
            timeout_seconds=validation_timeout,
        )
        outcome.status = "published_this_run"
        outcome.sync_status = "queued_by_outbox"
        return outcome
    except (
        FlowError,
        SQLAlchemyError,
        httpx.HTTPError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        outcome.status = "failed"
        outcome.error = str(exc)
        outcome.error_type = type(exc).__name__
        outcome.error_stage = stage
        outcome.retryable = is_retryable_exception(exc)
        outcome.attempt_errors.append(
            {
                "attempt": attempt_number,
                "error_type": outcome.error_type,
                "error_stage": stage,
                "message": outcome.error,
                "retryable": outcome.retryable,
            }
        )
        return outcome


def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, OperationalError):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


def read_source_document_ids(path: Path) -> set[str]:
    source_ids = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if not source_ids:
        raise FlowError(f"Source document file is empty: {path}")
    return source_ids


def read_source_publish_dates(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"document_id", "crawl_date"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise FlowError(
                f"Source metadata CSV must contain {sorted(required)}: {path}"
            )
        result: dict[str, str] = {}
        for line_number, row in enumerate(reader, start=2):
            document_id = str(row.get("document_id") or "").strip()
            crawl_date = str(row.get("crawl_date") or "").strip()
            if not document_id:
                raise FlowError(f"Missing document_id at {path}:{line_number}")
            try:
                parsed = date.fromisoformat(crawl_date)
            except ValueError as exc:
                raise FlowError(
                    f"Invalid crawl_date at {path}:{line_number}: {crawl_date!r}"
                ) from exc
            normalized = parsed.isoformat()
            previous = result.get(document_id)
            if previous is not None and previous != normalized:
                raise FlowError(
                    f"Conflicting crawl_date values for {document_id}: "
                    f"{previous} and {normalized}"
                )
            result[document_id] = normalized
    return result


def read_source_selection(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise FlowError(f"Source selection must contain a records array: {path}")
    result: dict[str, str] = {}
    for index, row in enumerate(payload["records"], start=1):
        if not isinstance(row, dict):
            raise FlowError(f"Invalid source selection record {index}: {path}")
        document_id = str(row.get("document_id") or "").strip()
        raw_date = str(row.get("crawl_date") or "").strip()
        if not document_id:
            raise FlowError(f"Missing document_id in source selection record {index}")
        try:
            normalized = date.fromisoformat(raw_date).isoformat()
        except ValueError as exc:
            raise FlowError(
                f"Invalid crawl_date in source selection record {index}: {raw_date!r}"
            ) from exc
        previous = result.get(document_id)
        if previous is not None and previous != normalized:
            raise FlowError(f"Conflicting source dates for {document_id}")
        result[document_id] = normalized
    expected = int(payload.get("selection_count") or 0)
    if not result or len(result) != expected:
        raise FlowError(
            f"Source selection count mismatch: manifest={expected}, unique={len(result)}"
        )
    return result


def read_source_selection_platforms(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise FlowError(f"Source selection must contain a records array: {path}")
    result: dict[str, str] = {}
    for index, row in enumerate(records, start=1):
        if not isinstance(row, dict):
            raise FlowError(f"Invalid source selection record {index}: {path}")
        document_id = str(row.get("document_id") or "").strip()
        platform = str(row.get("source_platform") or "").strip().casefold()
        if not document_id:
            raise FlowError(f"Missing document_id in source selection record {index}")
        # Other curated selection manifests predate platform provenance. They
        # remain importable; the formal 214-JD manifest supplies this field.
        if not platform:
            continue
        previous = result.get(document_id)
        if previous is not None and previous != platform:
            raise FlowError(f"Conflicting source platforms for {document_id}")
        result[document_id] = platform
    return result


def run_concurrent_pass(
    records: list[PreparedRecord],
    *,
    workers: int,
    max_in_flight: int,
    pass_number: int,
    submit_one,
) -> list[RecordOutcome]:
    outcomes: list[RecordOutcome] = []
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"jd-import-{pass_number}",
    ) as executor:
        record_iterator = iter(records)
        futures = {}
        for _ in range(min(max_in_flight, len(records))):
            record = next(record_iterator)
            futures[executor.submit(submit_one, record)] = record
        completed_count = 0
        while futures:
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                futures.pop(future)
                outcomes.append(future.result())
                completed_count += 1
                try:
                    record = next(record_iterator)
                except StopIteration:
                    pass
                else:
                    futures[executor.submit(submit_one, record)] = record
            if completed_count % 25 < len(completed) or completed_count == len(records):
                counts = Counter(item.status for item in outcomes)
                print(
                    f"[CONCURRENT-JD-IMPORT pass={pass_number}] "
                    f"{completed_count}/{len(records)} {dict(sorted(counts.items()))}",
                    flush=True,
                )
    return outcomes


def main() -> int:
    args = parse_args()
    if not 1 <= args.workers <= 512:
        raise ValueError("--workers must be between 1 and 512")
    if not 1 <= args.http_connections <= 192:
        raise ValueError("--http-connections must be between 1 and 192")
    if args.validation_timeout < 0:
        raise ValueError("--validation-timeout must be non-negative")
    if not 0 <= args.retry_passes <= 5:
        raise ValueError("--retry-passes must be between 0 and 5")
    max_in_flight = args.max_in_flight or args.workers * 2
    if not args.workers <= max_in_flight <= 2048:
        raise ValueError("--max-in-flight must be between --workers and 2048")

    print("[CONCURRENT-JD-IMPORT] acquiring exclusive import lock", flush=True)
    with exclusive_import_process() as database, project_tempdir(args.work_dir) as temp_dir:
        print("[CONCURRENT-JD-IMPORT] reading clean reviewed package", flush=True)
        batch_root = locate_batch_root(args.input, temp_dir)
        batch_name = args.batch_name or batch_root.name
        if args.source_selection_file and (
            args.only_source_document_file or args.source_metadata_file
        ):
            raise FlowError(
                "--source-selection-file cannot be combined with the separate "
                "source document or metadata files"
            )
        selection_dates = (
            read_source_selection(args.source_selection_file)
            if args.source_selection_file
            else None
        )
        selection_platforms = (
            read_source_selection_platforms(args.source_selection_file)
            if args.source_selection_file
            else {}
        )
        requested_source_ids = (
            set(selection_dates)
            if selection_dates is not None
            else read_source_document_ids(args.only_source_document_file)
            if args.only_source_document_file
            else None
        )
        print("[CONCURRENT-JD-IMPORT] validating selected clean records", flush=True)
        records, failed_cases, preparation = prepare_records(
            batch_root,
            max_records=args.max_records,
            only_families=set(args.only_family),
            skipped_source_ids=set(args.skip_source_document),
            only_source_ids=requested_source_ids,
        )
        if requested_source_ids is not None:
            available_source_ids = {row.source_document_id for row in records}
            missing_source_ids = sorted(requested_source_ids - available_source_ids)
            if missing_source_ids:
                raise FlowError(
                    "Requested source document IDs are absent from the input package: "
                    + ", ".join(missing_source_ids[:10])
                )
            preparation["source_document_filter_count"] = len(records)
        source_publish_dates: dict[str, str] = selection_dates or {}
        if args.source_metadata_file:
            source_publish_dates = read_source_publish_dates(args.source_metadata_file)
        if source_publish_dates:
            selected_source_ids = {row.source_document_id for row in records}
            missing_metadata = sorted(selected_source_ids - source_publish_dates.keys())
            if missing_metadata:
                raise FlowError(
                    "Selected records are missing Bundle crawl_date metadata: "
                    + ", ".join(missing_metadata[:10])
                )
            preparation["bundle_time_metadata_count"] = len(selected_source_ids)
        print(
            f"[CONCURRENT-JD-IMPORT] prepared {len(records)} records; "
            "synchronizing authoritative catalogs",
            flush=True,
        )
        summary = build_group_summary(records)

        control_api = API(args.main_url, args.timeout)
        try:
            control_api.ready()
            token = login_main(control_api, args.main_username, args.main_password)
            resolved_records = [row for row in records if row.classification_resolved]
            catalog_skills = ensure_main_catalog_skills(control_api, token, resolved_records)
            taxonomy_positions = ensure_taxonomy_positions(control_api, token, resolved_records)
            print(
                "[CONCURRENT-JD-IMPORT] catalogs ready; starting lifecycle replay",
                flush=True,
            )
            records_by_id = {row.source_document_id: row for row in records}
            final_outcomes: dict[str, RecordOutcome] = {}
            pending_records = records
            retry_passes_executed = 0
            for pass_index in range(args.retry_passes + 1):
                pass_number = pass_index + 1
                existing_jds = control_api.request("GET", "/api/v1/jds", token=token)["data"]
                existing_by_source_name = {
                    str(item["source_name"]): item
                    for item in existing_jds
                    if item.get("source_name")
                }
                worker_api = API(
                    args.main_url,
                    args.timeout,
                    max_connections=args.http_connections,
                )
                try:
                    def submit_one(record: PreparedRecord) -> RecordOutcome:
                        return import_record(
                            api=worker_api,
                            token=token,
                            batch_name=batch_name,
                            record=record,
                            existing_by_source_name=existing_by_source_name,
                            taxonomy_positions=taxonomy_positions,
                            catalog_skills=catalog_skills,
                            approve_validation_warnings=args.approve_validation_warnings,
                            validation_timeout=args.validation_timeout,
                            database=database,
                            attempt_number=pass_number,
                            publish_date=source_publish_dates.get(record.source_document_id),
                            source_platform=selection_platforms.get(record.source_document_id),
                        )

                    pass_outcomes = run_concurrent_pass(
                        pending_records,
                        workers=args.workers,
                        max_in_flight=max_in_flight,
                        pass_number=pass_number,
                        submit_one=submit_one,
                    )
                finally:
                    worker_api.close()

                retry_ids: list[str] = []
                for outcome in pass_outcomes:
                    previous = final_outcomes.get(outcome.source_document_id)
                    if previous is not None:
                        outcome.attempt_errors = [
                            *previous.attempt_errors,
                            *outcome.attempt_errors,
                        ]
                    final_outcomes[outcome.source_document_id] = outcome
                    if outcome.status == "failed" and outcome.retryable:
                        retry_ids.append(outcome.source_document_id)
                if not retry_ids or pass_index == args.retry_passes:
                    break
                retry_passes_executed += 1
                pending_records = [records_by_id[source_id] for source_id in retry_ids]
                print(
                    f"[CONCURRENT-JD-IMPORT] retrying {len(pending_records)} "
                    f"transient failures with a fresh HTTP connection pool",
                    flush=True,
                )

            outcomes = sorted(
                final_outcomes.values(),
                key=lambda item: item.source_document_id,
            )
            counts = Counter(item.status for item in outcomes)
            report = {
                "status": "success" if not counts["failed"] else "partial_success",
                "input": str(args.input),
                "batch_name": batch_name,
                "workers": args.workers,
                "http_connections": args.http_connections,
                "max_in_flight": max_in_flight,
                "retry_passes_requested": args.retry_passes,
                "retry_passes_executed": retry_passes_executed,
                "summary": summary,
                "preparation": preparation,
                "source_failed_count": len(failed_cases),
                "source_failed_cases": failed_cases,
                "result_counts": dict(sorted(counts.items())),
                "result_count_meanings": RESULT_COUNT_MEANINGS,
                "records": [asdict(item) for item in outcomes],
                "delivery": "Published JD facts are delivered by kg-outbox-worker.",
            }
            write_report(args.report, report)
            print(json.dumps(report["result_counts"], ensure_ascii=False))
            if counts["failed"] and not args.continue_on_error:
                return 1
            return 0
        finally:
            control_api.close()


if __name__ == "__main__":
    raise SystemExit(main())
