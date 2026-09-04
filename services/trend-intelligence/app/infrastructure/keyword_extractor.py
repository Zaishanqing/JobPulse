from __future__ import annotations

import yake

from app.domain.market import ExtractedTerm, StoredSnapshot, week_start


class YakeKeywordExtractor:
    version = "yake.v1"

    def __init__(self, *, max_terms: int = 8) -> None:
        self.max_terms = max_terms
        self._english = yake.KeywordExtractor(lan="en", n=2, dedupLim=0.9, top=max_terms)
        self._chinese = yake.KeywordExtractor(lan="zh", n=2, dedupLim=0.9, top=max_terms)

    def extract(self, snapshot: StoredSnapshot) -> list[ExtractedTerm]:
        text = f"{snapshot.record.title} {snapshot.record.content}".strip()
        if len(text) < 20:
            return []
        chinese_count = sum("\u4e00" <= character <= "\u9fff" for character in text)
        extractor = self._chinese if chinese_count > len(text) * 0.1 else self._english
        terms = extractor.extract_keywords(text)
        return [
            ExtractedTerm(snapshot_id=snapshot.id, term=term.lower().strip(), score=round(1.0 - min(float(score), 1.0), 6), week_start=week_start(snapshot.record.published_at), extractor_version=self.version)
            for term, score in terms[: self.max_terms]
            if term.strip()
        ]
