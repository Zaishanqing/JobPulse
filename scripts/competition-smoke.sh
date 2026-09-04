#!/usr/bin/env bash
set -euo pipefail

# Locate the repository root directly from this script path.
repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repository_root/infra/compose/docker-compose.candidate.yml"
compose_project_directory="$repository_root/infra"
business_smoke_script="$repository_root/apps/api/scripts/competition_business_smoke.py"
frontend_root="$repository_root/apps/web"
timeout_seconds="${COMPETITION_SMOKE_TIMEOUT_SECONDS:-600}"
base_url="${COMPETITION_SMOKE_BASE_URL:-http://127.0.0.1:8000}"
project_name="${COMPETITION_SMOKE_PROJECT_NAME:-jobpulse-competition-smoke}"
profile="${COMPOSE_PROFILE:-}"
evidence_dir="${COMPETITION_SMOKE_EVIDENCE_DIR:-$repository_root/reports/compose-smoke}"
if [[ "$evidence_dir" != /* ]]; then
  evidence_dir="$repository_root/$evidence_dir"
fi
profile_args=()
reset_args=()
if [ -n "$profile" ]; then
  profile_args=(--profile "$profile")
  project_name="${project_name}-${profile}"
fi
if [ "${COMPETITION_SMOKE_RESET_VOLUMES:-false}" = "true" ]; then
  reset_args=(-v)
fi
compose=(docker compose --project-directory "$compose_project_directory" --project-name "$project_name" -f "$compose_file" "${profile_args[@]}")

if [ ! -f "$compose_file" ]; then
  printf 'competition smoke compose file is missing: %s\n' "$compose_file" >&2
  exit 1
fi
if [ ! -f "$business_smoke_script" ]; then
  printf 'competition smoke business script is missing: %s\n' "$business_smoke_script" >&2
  exit 1
fi
if [ ! -f "$frontend_root/package-lock.json" ]; then
  printf 'competition smoke frontend lockfile is missing: %s\n' "$frontend_root/package-lock.json" >&2
  exit 1
fi

cleanup() {
  printf '\n=== competition smoke: cleanup ===\n'
  # Preserve database/model volumes so a local smoke cannot erase user data.
  # CI runners are ephemeral; explicit volume cleanup remains an operator action.
  timeout 60 "${compose[@]}" down "${reset_args[@]}" --remove-orphans || true
}
trap cleanup EXIT

capture_evidence() {
  mkdir -p "$evidence_dir"
  {
    printf 'profile=%s\n' "${profile:-default}"
    printf 'project_name=%s\n' "$project_name"
    printf 'git_sha=%s\n' "${GITHUB_SHA:-unknown}"
    printf '\n=== compose ps ===\n'
    "${compose[@]}" ps || true
    printf '\n=== compose config services ===\n'
    "${compose[@]}" config --services || true
  } > "$evidence_dir/${profile:-default}.txt"
}

fail() {
  printf '\n=== competition smoke: FAILED ===\n'
  printf '%s\n' "$1"
  "${compose[@]}" logs --no-color --tail=200 \
    main-backend extraction-worker validation-worker kg-outbox-worker \
    main-postgres knowledge-graph-backend knowledge-graph-worker \
    matching-api matching-worker matching-dispatcher \
    emerging-discovery trend-intelligence trend-intelligence-worker \
    cv-extraction cv-extraction-worker jd-extraction embedding-service \
    crawler-api crawler-scheduler crawler-mysql \
    matching-api-semantic-demo matching-vector-worker-semantic-demo || true
  capture_evidence
  exit 1
}

wait_for_url() {
  local url="$1"
  local deadline=$((SECONDS + timeout_seconds))
  until curl -fsS "$url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      fail "$url did not become ready"
    fi
    sleep 2
  done
}

probe_internal() {
  local service="$1"
  local path="$2"
  "${compose[@]}" exec -T "$service" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000${path}', timeout=5).read()"
}

printf '=== competition smoke: frontend production build ===\n'
npm ci --prefix "$frontend_root"
npm run build --prefix "$frontend_root"

printf '=== competition smoke: compose config ===\n'
timeout "$timeout_seconds" "${compose[@]}" config --quiet

if [ "${COMPETITION_SMOKE_RESET_VOLUMES:-false}" = "true" ]; then
  printf '=== competition smoke: reset environment volumes ===\n'
  timeout 60 "${compose[@]}" down "${reset_args[@]}" --remove-orphans || true
fi

# apps/api/Dockerfile FROMs a local base image (Tesseract OCR layer) that
# compose --build cannot pull, so build it first. Its context is empty by
# design; the Dockerfile COPYs nothing.
printf '=== competition smoke: main backend base image ===\n'
base_context="$(mktemp -d)"
docker build --tag jobpulse-main-backend-base:candidate \
  --file "$repository_root/apps/api/Dockerfile.base" "$base_context"
rmdir "$base_context"

printf '=== competition smoke: build service images ===\n'
timeout "$timeout_seconds" "${compose[@]}" build \
  || fail "docker compose build failed"

printf '=== competition smoke: start services ===\n'
timeout "$timeout_seconds" "${compose[@]}" up -d --wait \
  || fail "docker compose up --wait failed"

printf '=== competition smoke: default async workers ===\n'
workers=(validation-worker kg-outbox-worker knowledge-graph-worker \
  matching-worker matching-dispatcher trend-intelligence-worker)
if [ "$profile" = "model-extraction" ]; then
  workers+=(extraction-worker)
fi
if [ "$profile" = "cv-extraction" ]; then
  workers+=(cv-extraction-worker)
fi
if [ "$profile" = "crawler" ]; then
  workers+=(crawler-scheduler)
fi
if [ "$profile" = "full" ]; then
  workers+=(extraction-worker cv-extraction-worker crawler-scheduler \
    matching-vector-worker-semantic-demo)
fi
for worker in "${workers[@]}"; do
  if ! "${compose[@]}" ps --status running --services | grep -Fxq "$worker"; then
    fail "$worker is not running"
  fi
done

if [ "$profile" = "cv-extraction" ] || [ "$profile" = "full" ]; then
  probe_internal cv-extraction /health \
    || fail "cv-extraction /health did not return 2xx"
fi
if [ "$profile" = "model-extraction" ] || [ "$profile" = "full" ]; then
  probe_internal jd-extraction /readiness \
    || fail "jd-extraction /readiness did not return 2xx"
fi
if [ "$profile" = "crawler" ] || [ "$profile" = "full" ]; then
  probe_internal crawler-api /api/health \
    || fail "crawler-api /api/health did not return 2xx"
fi
if [ "$profile" = "semantic-demo" ] || [ "$profile" = "full" ]; then
  probe_internal embedding-service /ready \
    || fail "embedding-service /ready did not return 2xx"
  probe_internal matching-api-semantic-demo /health/ready \
    || fail "matching-api-semantic-demo /health/ready did not return 2xx"
fi

printf '=== competition smoke: wait for health/readiness ===\n'
wait_for_url "$base_url/health"
wait_for_url "$base_url/readiness"

printf '=== competition smoke: dependent service health ===\n'
probe_internal knowledge-graph-backend /health \
  || fail "knowledge-graph-backend /health did not return 2xx"
probe_internal matching-api /health/ready \
  || fail "matching-api /health/ready did not return 2xx"
if [ "${MATCHING_RESPONSIBILITY_CE_MODE:-disabled}" = "enabled" ]; then
  printf '=== competition smoke: verified Responsibility CE readiness ===\n'
  mkdir -p "$evidence_dir"
  if ! "${compose[@]}" exec -T matching-api python -m app.diagnostics.verify_readiness \
      | tee -a "$evidence_dir/${profile:-default}.txt"; then
    fail "matching-api Responsibility CE readiness or artifact digest verification failed"
  fi
fi
probe_internal emerging-discovery /readiness \
  || fail "emerging-discovery /readiness did not return 2xx"
probe_internal trend-intelligence /readiness \
  || fail "trend-intelligence /readiness did not return 2xx"

printf '=== competition smoke: authenticated business projections ===\n'
python3 "$business_smoke_script" \
  || fail "authenticated competition business smoke failed"

printf '\n=== competition smoke: PASSED ===\n'
"${compose[@]}" ps
capture_evidence
