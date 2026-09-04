"""Small dependency-free Prometheus text registry."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock

LabelKey = tuple[tuple[str, str], ...]


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, LabelKey], float] = defaultdict(float)
        self._gauges: dict[tuple[str, LabelKey], float] = {}
        self._duration_count: dict[tuple[str, LabelKey], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, LabelKey], float] = defaultdict(float)
        self._lock = RLock()

    def increment(self, name: str, value: float = 1, **labels: str) -> None:
        with self._lock:
            self._counters[(name, self._labels(labels))] += value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[(name, self._labels(labels))] = value

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            self._duration_count[key] += 1
            self._duration_sum[key] += max(seconds, 0)

    def counter_value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get((name, self._labels(labels)), 0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}{self._format_labels(labels)} {self._number(value)}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{self._format_labels(labels)} {self._number(value)}")
            duration_keys = sorted(self._duration_count)
            for name, labels in duration_keys:
                count = self._duration_count[(name, labels)]
                total = self._duration_sum[(name, labels)]
                rendered = self._format_labels(labels)
                lines.append(f"{name}_count{rendered} {count}")
                lines.append(f"{name}_sum{rendered} {self._number(total)}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _labels(labels: dict[str, str]) -> LabelKey:
        return tuple(sorted((key, str(value)) for key, value in labels.items()))

    @staticmethod
    def _format_labels(labels: LabelKey) -> str:
        if not labels:
            return ""
        values = ",".join(
            f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for key, value in labels
        )
        return "{" + values + "}"

    @staticmethod
    def _number(value: float) -> str:
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else f"{numeric:.9f}".rstrip("0")
