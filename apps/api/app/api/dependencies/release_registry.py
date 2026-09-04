from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import Request

from app.contexts.insight_cards.release_registry import (
    ManifestReleaseRegistry,
    ReleaseRegistry,
)


def _base_dir() -> Path:
    configured = os.environ.get("INSIGHT_RELEASE_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data" / "releases"


@lru_cache(maxsize=1)
def _registry() -> ManifestReleaseRegistry:
    return ManifestReleaseRegistry(_base_dir())


def get_release_registry(request: Request) -> ReleaseRegistry:
    return _registry()
