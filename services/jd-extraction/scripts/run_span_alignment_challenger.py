"""B-JD-REAL span-first alignment challenger.

Re-aligns every prediction requirement evidence quote against the full JD
text with a deterministic three-stage matcher:

1. exact substring
2. normalized exact (NFKC, whitespace/punctuation/case folded)
3. token-level fuzzy alignment with a score and explicit start/end

When a quote occurs multiple times, the occurrence closest to the original
prediction offset is preferred so repeated terms (summary vs body) do not
systematically point at the first match.

Quotes that cannot be aligned above the threshold stay ``unresolved`` and are
never fabricated.  Metrics: exact span F1, relaxed span F1, mean boundary
error, unresolved rate and hallucinated-span rate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


FUZZY_THRESHOLD = 0.72


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(
            Path(__file__).resolve().parents[1]
            / "output"
            / "evaluation"
            / "real-requirement-graph"
            / "real-jd-gold-manifest.json"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1]
            / "output"
            / "evaluation"
            / "real-requirement-graph"
            / "span-aligner-v3"
        ),
    )
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    cases: list[dict] = []
    for case in manifest["cases"]:
        aligned = [
            _align_requirement(requirement, case["jd_text"])
            for requirement in case["requirements"]
        ]
        gold_by_id = {
            str(requirement["requirement_id"]): requirement
            for requirement in case["reviewed_requirements"]
        }
        case_metrics = _case_metrics(aligned, gold_by_id, case["jd_text"])
        cases.append(
            {
                "case_id": case["case_id"],
                "metrics": case_metrics,
                "aligned_requirements": aligned,
            }
        )
    report = {
        "schema_version": "jd-span-aligner-v3.v2",
        "experiment_id": "B-JD-REAL-01",
        "challenger": "span-first-aligner.v3 (closest-occurrence preference)",
        "dataset_version": manifest.get("dataset_version"),
        "gold_version": manifest.get("gold_policy", {}).get("gold_version"),
        "fuzzy_threshold": FUZZY_THRESHOLD,
        "occurrence_policy": (
            "prefer the occurrence nearest to the original prediction offset"
        ),
        "aggregate": _aggregate(cases),
        "cases": cases,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "span-alignment-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "TAB-SPAN-V3.md").write_text(
        _render_table(report), encoding="utf-8"
    )
    print(out_dir / "span-alignment-results.json")
    return 0


def _align_requirement(requirement: dict, jd_text: str) -> dict:
    evidence = requirement.get("evidence") or {}
    quote = str(evidence.get("quote") or "")
    aligned = _align_span(
        quote,
        jd_text,
        preferred_offset=evidence.get("start"),
    )
    return {
        "requirement_id": requirement["requirement_id"],
        "original_quote": quote,
        "original_start": evidence.get("start"),
        "original_end": evidence.get("end"),
        "aligned_quote": aligned["quote"],
        "start": aligned["start"],
        "end": aligned["end"],
        "alignment": aligned["alignment"],
        "alignment_score": aligned["score"],
    }


def _align_span(
    quote: str,
    jd_text: str,
    preferred_offset: int | None = None,
) -> dict:
    """Three-stage deterministic span alignment."""

    if not quote or not jd_text:
        return {
            "quote": None,
            "start": None,
            "end": None,
            "alignment": "unresolved",
            "score": 0.0,
        }
    exact_candidates = _exact_occurrences(quote, jd_text)
    if exact_candidates:
        start, end = _pick_occurrence(exact_candidates, preferred_offset)
        return {
            "quote": jd_text[start:end],
            "start": start,
            "end": end,
            "alignment": "exact",
            "score": 1.0,
        }
    normalized_quote = _normalize(quote)
    normalized_text = _normalize(jd_text)
    normalized_candidates = _normalized_occurrences(
        normalized_quote, normalized_text, jd_text
    )
    if normalized_candidates:
        start, end = _pick_occurrence(normalized_candidates, preferred_offset)
        return {
            "quote": jd_text[start:end],
            "start": start,
            "end": end,
            "alignment": "normalized_exact",
            "score": 0.95,
        }
    fuzzy = _fuzzy_align(quote, jd_text, preferred_offset)
    if fuzzy is not None:
        return fuzzy
    return {
        "quote": None,
        "start": None,
        "end": None,
        "alignment": "unresolved",
        "score": 0.0,
    }


def _exact_occurrences(quote: str, jd_text: str) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    start = jd_text.find(quote)
    while start >= 0:
        candidates.append((start, start + len(quote)))
        start = jd_text.find(quote, start + 1)
    return candidates


def _normalized_occurrences(
    normalized_quote: str,
    normalized_text: str,
    jd_text: str,
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    normalized_text, index_map = _normalized_index_map(jd_text)
    normalized_start = normalized_text.find(normalized_quote)
    while normalized_start >= 0:
        start = _map_index(index_map, normalized_start)
        end = _map_index(
            index_map, normalized_start + len(normalized_quote)
        )
        candidates.append((start, end))
        normalized_start = normalized_text.find(
            normalized_quote, normalized_start + 1
        )
    return candidates


def _normalized_index_map(
    jd_text: str,
) -> tuple[str, list[tuple[int, int]]]:
    normalized_chars: list[str] = []
    index_map: list[tuple[int, int]] = []
    for index, char in enumerate(jd_text):
        if re.match(r"\s", char):
            continue
        normalized_char = _normalize(char)
        if normalized_char:
            normalized_chars.append(normalized_char)
            index_map.append((index, len(normalized_char)))
    return "".join(normalized_chars), index_map


def _map_index(
    index_map: list[tuple[int, int]],
    normalized_index: int,
) -> int:
    normalized_run = 0
    for original_index, char_length in index_map:
        if normalized_run <= normalized_index < normalized_run + char_length:
            return original_index
        if normalized_index == normalized_run + char_length:
            return original_index + 1
        normalized_run += char_length
    return 0


def _pick_occurrence(
    candidates: list[tuple[int, int]],
    preferred_offset: int | None,
) -> tuple[int, int]:
    if preferred_offset is None or len(candidates) <= 1:
        return candidates[0]
    return min(
        candidates,
        key=lambda item: (abs(item[0] - preferred_offset), item[0]),
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


def _original_index(jd_text: str, normalized_index: int) -> int:
    """Map a normalized-text index back to the original JD index."""

    normalized_run = 0
    for index, char in enumerate(jd_text):
        if re.match(r"\s", char):
            continue
        normalized_char = _normalize(char)
        if normalized_char:
            char_length = len(normalized_char)
            if normalized_run <= normalized_index < normalized_run + char_length:
                return index
            if normalized_index == normalized_run + char_length:
                return index + 1
            normalized_run += char_length
    return 0


def _fuzzy_align(
    quote: str,
    jd_text: str,
    preferred_offset: int | None = None,
) -> dict | None:
    quote_spans = _fuzzy_term_spans(quote)
    if not quote_spans:
        return None
    quote_terms = [term for term, _start, _end in quote_spans]
    text_spans = _fuzzy_term_spans(jd_text)
    text_terms = [term for term, _start, _end in text_spans]
    best = 0.0
    best_windows: list[tuple[int, int]] = []
    min_window = max(1, math.ceil(len(quote_terms) * 0.8))
    max_window = max(min_window, int(len(quote_terms) * 1.2) + 1)
    for start in range(len(text_terms)):
        for size in range(
            min_window,
            min(max_window, len(text_terms) - start) + 1,
        ):
            window = text_terms[start : start + size]
            score = _fuzzy_score(quote_terms, window)
            if score > best + 1e-9:
                best = score
                best_windows = [(start, start + size)]
            elif abs(score - best) <= 1e-9:
                best_windows.append((start, start + size))
    if not best_windows or best < FUZZY_THRESHOLD:
        return None
    best_window = min(
        best_windows,
        key=lambda item: (
            abs(text_spans[item[0]][1] - (preferred_offset or 0)),
            item[0],
        ),
    )
    start_term, end_term = best_window
    start = text_spans[start_term][1]
    end = text_spans[end_term - 1][2]
    return {
        "quote": jd_text[start:end],
        "start": start,
        "end": end,
        "alignment": "fuzzy",
        "score": round(best, 4),
    }


def _tokenize(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", value.casefold())
        if token
    ]


def _fuzzy_term_spans(value: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for match in re.finditer(r"[\w\u4e00-\u9fff]+", value.casefold()):
        start, end = match.span()
        term = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) >= 2:
            for index in range(len(term) - 1):
                spans.append((term[index : index + 2], start, end))
        else:
            spans.append((term, start, end))
    return spans


def _fuzzy_score(left: list[str], right: list[str]) -> float:
    return round(
        max(
            _dice(left, right),
            0.5 * _dice(left, right)
            + 0.3 * _order_similarity(left, right)
            + 0.2 * _char_bigram_similarity(left, right),
        ),
        6,
    )


def _order_similarity(left: list[str], right: list[str]) -> float:
    right_positions = {
        term: index for index, term in enumerate(right)
    }
    order = [
        right_positions[term]
        for term in left
        if term in right_positions
    ]
    if len(order) < 2:
        return 0.0
    return sum(
        1 for first, second in zip(order, order[1:]) if first < second
    ) / (len(order) - 1)


def _char_bigram_similarity(left: list[str], right: list[str]) -> float:
    def bigrams(terms: list[str]) -> set[str]:
        value = "".join(terms)
        return {value[index : index + 2] for index in range(len(value) - 1)}

    left_bigrams = bigrams(left)
    right_bigrams = bigrams(right)
    if not left_bigrams or not right_bigrams:
        return 0.0
    return 2 * len(left_bigrams & right_bigrams) / (
        len(left_bigrams) + len(right_bigrams)
    )


def _dice(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return 2 * len(left_set & right_set) / (len(left_set) + len(right_set))


def _term_start(jd_text: str, terms: list[str], index: int) -> int:
    position = 0
    for term_index, term in enumerate(_tokenize(jd_text)):
        position = jd_text.casefold().find(term, position)
        if term_index == index:
            return position
        position += len(term)
    return 0


def _term_end(jd_text: str, terms: list[str], index: int) -> int:
    start = _term_start(jd_text, terms, index)
    return start + len(terms[index])


def _case_metrics(
    aligned: list[dict], gold_by_id: dict[str, dict], jd_text: str
) -> dict:
    exact = relaxed = boundary_error_sum = boundary_count = 0
    boundary_errors: list[float] = []
    unresolved = hallucinated = multi_instance = 0
    prediction_count = len(aligned)
    gold_count = len(gold_by_id)
    matched_gold = 0
    for item in aligned:
        gold = gold_by_id.get(str(item["requirement_id"]))
        if gold is None:
            continue
        gold_evidence = gold.get("evidence") or {}
        gold_start = gold_evidence.get("start")
        gold_end = gold_evidence.get("end")
        if item["start"] is None:
            unresolved += 1
            continue
        if gold_start is None or gold_end is None:
            continue
        matched_gold += 1
        if item["aligned_quote"] and jd_text.count(item["aligned_quote"]) > 1:
            multi_instance += 1
        if item["start"] == gold_start and item["end"] == gold_end:
            exact += 1
            relaxed += 1
        else:
            overlap_start = max(item["start"], gold_start)
            overlap_end = min(item["end"], gold_end)
            overlap = max(overlap_end - overlap_start, 0)
            union = max(item["end"] - item["start"] + gold_end - gold_start - overlap, 1)
            if overlap / union >= 0.8:
                relaxed += 1
        boundary_error_sum += abs(item["start"] - gold_start) + abs(item["end"] - gold_end)
        boundary_count += 1
        boundary_errors.append(
            abs(item["start"] - gold_start) + abs(item["end"] - gold_end)
        )
    for item in aligned:
        if (
            item["start"] is not None
            and item["aligned_quote"] is not None
            and item["aligned_quote"] not in jd_text
        ):
            hallucinated += 1
    return {
        "prediction_count": prediction_count,
        "gold_count": gold_count,
        "exact_span_count": exact,
        "relaxed_span_count": relaxed,
        "unresolved_count": unresolved,
        "boundary_error": (
            round(boundary_error_sum / max(boundary_count, 1), 2)
            if boundary_count
            else None
        ),
        "boundary_errors": boundary_errors,
        "hallucinated_count": hallucinated,
        "multi_instance_count": multi_instance,
    }


def _aggregate(cases: list[dict]) -> dict:
    prediction_count = sum(case["metrics"]["prediction_count"] for case in cases)
    gold_count = sum(case["metrics"]["gold_count"] for case in cases)
    exact = sum(case["metrics"]["exact_span_count"] for case in cases)
    relaxed = sum(case["metrics"]["relaxed_span_count"] for case in cases)
    unresolved = sum(case["metrics"]["unresolved_count"] for case in cases)
    hallucinated = sum(case["metrics"]["hallucinated_count"] for case in cases)
    multi_instance = sum(case["metrics"]["multi_instance_count"] for case in cases)
    boundary_errors = [
        value
        for case in cases
        for value in case["metrics"].get("boundary_errors", [])
    ]
    precision = exact / prediction_count if prediction_count else None
    recall = exact / gold_count if gold_count else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    relaxed_precision = relaxed / prediction_count if prediction_count else None
    relaxed_recall = relaxed / gold_count if gold_count else None
    relaxed_f1 = (
        2 * relaxed_precision * relaxed_recall / (relaxed_precision + relaxed_recall)
        if relaxed_precision is not None
        and relaxed_recall is not None
        and relaxed_precision + relaxed_recall
        else None
    )
    return {
        "prediction_count": prediction_count,
        "gold_count": gold_count,
        "exact_span_f1": round(f1, 6) if f1 is not None else None,
        "exact_span_precision": round(precision, 6) if precision is not None else None,
        "exact_span_recall": round(recall, 6) if recall is not None else None,
        "relaxed_span_f1": round(relaxed_f1, 6) if relaxed_f1 is not None else None,
        "mean_boundary_error": (
            round(sum(boundary_errors) / len(boundary_errors), 2)
            if boundary_errors
            else None
        ),
        "unresolved_rate": round(unresolved / prediction_count, 6)
        if prediction_count
        else None,
        "hallucinated_span_rate": round(hallucinated / prediction_count, 6)
        if prediction_count
        else None,
        "multi_instance_rate": round(multi_instance / prediction_count, 6)
        if prediction_count
        else None,
    }


def _render_table(report: dict) -> str:
    agg = report["aggregate"]
    lines = [
        "# TAB-SPAN-V3 B-JD-REAL span-first aligner challenger",
        "",
        f"- 阈值：fuzzy >= {report['fuzzy_threshold']}",
        "",
        "| Exact Span F1 | Exact P/R | Relaxed Span F1 | Mean boundary error | "
        "Unresolved rate | Hallucinated rate | Multi-instance rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
        "| {f1} | {p} / {r} | {relaxed} | {boundary} | {unresolved} | "
        "{hallucinated} | {multi} |".format(
            f1=_display(agg["exact_span_f1"]),
            p=_display(agg["exact_span_precision"]),
            r=_display(agg["exact_span_recall"]),
            relaxed=_display(agg["relaxed_span_f1"]),
            boundary=_display(agg["mean_boundary_error"]),
            unresolved=_display(agg["unresolved_rate"]),
            hallucinated=_display(agg["hallucinated_span_rate"]),
            multi=_display(agg["multi_instance_rate"]),
        ),
    ]
    return "\n".join(lines)


def _display(value) -> str:
    return "-" if value is None else f"{value:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
