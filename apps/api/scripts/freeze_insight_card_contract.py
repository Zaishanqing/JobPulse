"""Freeze or check the shared InsightCard contract snapshot.

The snapshot is derived from ``app.contexts.insight_cards.contracts`` and is
written to both the repository docs and the frontend TypeScript contract
location.  CI should run ``--check`` so any domain/BFF contract drift fails
the test run instead of being silently regenerated.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
JOBPULSE_ROOT = FRAMEWORK_ROOT.parents[1]
sys.path.insert(0, str(FRAMEWORK_ROOT))

from app.contexts.insight_cards.contract_snapshot import (  # noqa: E402
    snapshot_json,
)

OUTPUTS = (
    FRAMEWORK_ROOT / "docs" / "insight-card-contract.v1.json",
    JOBPULSE_ROOT
    / "apps"
    / "web"
    / "src"
    / "features"
    / "insights"
    / "insight-card-contract.snapshot.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = snapshot_json()
    mismatches: list[Path] = []
    for path in OUTPUTS:
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            mismatches.append(path)

    if mismatches:
        if args.check:
            for path in mismatches:
                print(f"contract drift: {path}", file=sys.stderr)
            return 1
        for path in mismatches:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
            print(f"froze {path}")
    else:
        print("InsightCard contract snapshots are in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
