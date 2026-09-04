#!/usr/bin/env bash
set -euo pipefail

# Repo-level architecture, contract, routing, and ownership tests live here.
# Service-local suites remain with their service owners and are not repeated by
# this entrypoint.
jobpulse_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
pytest_basetemp="${TMPDIR:-/tmp}/jobpulse-repo-tests-$$-${RANDOM:-0}"

cd "$jobpulse_root/apps/api"
# JobPulse itself must be importable: tests reference scripts.* modules that
# live at the repository root (e.g. scripts.build_release_manifest).
export PYTHONPATH="$jobpulse_root:$jobpulse_root/apps/api:$jobpulse_root/packages/contracts${PYTHONPATH:+:$PYTHONPATH}"
python -m pytest "$jobpulse_root/tests" --basetemp "$pytest_basetemp"
