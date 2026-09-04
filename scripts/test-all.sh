#!/usr/bin/env bash
set -uo pipefail

# repository_root is the repository root (the parent of the scripts
# directory); module paths are relative to this root.
repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/module-path.sh"

suite="${1:?Usage: scripts/test-all.sh <suite> [<tree>]}"
TEST_TREE="${2:-${TEST_TREE:-$JOBPULSE_ROOT}}"
completed=()
pytest_basetemp="${TMPDIR:-/tmp}/jobpulse-pytest-$$-${RANDOM:-0}"
coverage_args=()

# Coverage is opt-in so the ordinary module gates and test commands retain
# their existing behaviour.  coverage-all.py enables this block for one
# module at a time and supplies the output directories.  The source arguments
# deliberately name only the module's formal source packages; tests,
# migrations and installed dependencies are outside these roots.
configure_coverage() {
  local suite="$1"
  coverage_args=()
  if [ -z "${JOBPULSE_COVERAGE_DIR:-}" ]; then
    return 0
  fi

  local coverage_dir="$JOBPULSE_COVERAGE_DIR"
  local junit_dir="${JOBPULSE_JUNIT_DIR:-$coverage_dir}"
  local slug="${JOBPULSE_COVERAGE_SLUG:?JOBPULSE_COVERAGE_SLUG is required when coverage is enabled}"
  local source
  local sources=()

  case "$suite" in
    Main|KnowledgeGraph|MatchingService|EmergingDiscovery|TrendIntelligence|EmbeddingService)
      sources=(app)
      ;;
    Crawler)
      sources=(
        patches.scheduler
        multi_company_scraper.adapters.crawler_jd_envelope
        multi_company_scraper.collector
        multi_company_scraper.models.company_config
        multi_company_scraper.models.job_data
        multi_company_scraper.normalizer
        multi_company_scraper.scrapers.base
        multi_company_scraper.scrapers.dispatcher
        multi_company_scraper.scrapers.liepin_scraper
        multi_company_scraper.scrapers.playwright_scraper
        unified_api.database
        unified_api.offline_export.staging
        unified_api.services.boss_detail
        unified_api.services.boss_service
        unified_api.services.company_service
        unified_api.services.liepin_service
        unified_api.services.persistence
        unified_api.services.task_manager
      )
      ;;
    JDExtraction)
      sources=(src)
      ;;
    CVExtraction)
      sources=(src api)
      ;;
    *)
      return 0
      ;;
  esac

  for source in "${sources[@]}"; do
    coverage_args+=("--cov=$source")
  done
  case "$suite" in
    Main) coverage_args+=(--cov-fail-under=78) ;;
    KnowledgeGraph|MatchingService) coverage_args+=(--cov-fail-under=85) ;;
  esac
  coverage_args+=(
    --cov-branch
    --cov-report=term-missing
    "--cov-report=json:${coverage_dir}/${slug}.json"
    "--cov-report=xml:${coverage_dir}/${slug}.xml"
    "--junitxml=${junit_dir}/${slug}.junit.xml"
  )
}

# Resolve the active module path for a suite.
module_path() {
  resolve_module_path "$1" "$TEST_TREE"
}

# Return the Main source root. Shared contracts and CI selectors intentionally
# do not belong to this source root.
main_source_root() {
  printf '%s' "apps/api"
}

# Test-only override used by the short classifier cases in test-routing.sh.
changed_files() {
  local base_sha="$1"
  if [ -n "${TEST_CHANGED_FILES:-}" ]; then
    printf '%s\n' "$TEST_CHANGED_FILES"
  else
    git diff --name-only "$base_sha...HEAD"
  fi
}

# Normalize a changed file path to the active Main tree's relative form.
module_rel() {
  local file="$1"
  local source_root
  source_root="$(main_source_root)" || return 2
  case "$file" in
    "$source_root"/*) printf '%s' "${file#"$source_root"/}"; return ;;
  esac
  printf '%s' "$file"
}

run_module() {
  local name="$1"
  local path="$2"
  shift 2

  printf '\n=== Testing: %s ===\n' "$name"
  pushd "$repository_root/$path" >/dev/null || return 1
  local status=0
  "$@" || status=$?
  local popd_status=0
  popd >/dev/null || popd_status=$?

  if (( status != 0 )); then
    printf '\n=== Test summary: FAILED ===\n'
    printf 'Failed module: %s (exit %d)\n' "$name" "$status"
    printf 'Completed modules: %s\n' "${completed[*]:-none}"
    return "$status"
  fi

  if (( popd_status != 0 )); then
    printf '\n=== Test summary: FAILED ===\n'
    printf 'Failed module: %s (could not restore working directory, exit %d)\n' "$name" "$popd_status"
    printf 'Completed modules: %s\n' "${completed[*]:-none}"
    return "$popd_status"
  fi

  completed+=("$name")
  printf '=== Passed: %s ===\n' "$name"
}

# These guardrails are owned by the Main module. Repository-level architecture
# and contract tests are run by tests/run-ci.sh instead of being
# repeated from apps/api.
main_core_guardrails=(
  tests/test_clean_architecture_discovery.py
  tests/test_context_boundaries.py
  tests/test_interface_coverage.py
  tests/test_schema_layering.py
)

main_requires_full() {
  local base_sha="$1"
  local source_root
  source_root="$(main_source_root)" || return 2
  local api_prefix="$source_root"
  # Main business code is deliberately simple: any app/** change gets the
  # full backend suite. Test-only changes outside app/** can still use the
  # small affected selection below.
  local pattern="${api_prefix}/app/|${api_prefix}/(alembic|migrations)/|${api_prefix}/alembic.ini|${api_prefix}/pyproject.toml|${api_prefix}/scripts/|${api_prefix}/tests/(conftest.py|runtime_database.py|fixtures/)"
  changed_files "$base_sha" | grep -E "$pattern" >/dev/null
}

main_affected_tests() {
  local base_sha="$1"
  local api_root
  local source_root
  api_root="$(module_path Main)"
  source_root="$(main_source_root)" || return 2
  local seen="|"
  local file rel module
  while IFS= read -r file; do
    case "$file" in
      "$source_root"/tests/test_*.py)
        rel="$(module_rel "$file")"
        if [[ "$seen" != *"|$rel|"* ]]; then seen="${seen}${rel}|"; printf '%s\n' "$rel"; fi
        ;;
      "$source_root"/app/*.py)
        rel="$(module_rel "$file")"
        module="${rel%.py}"
        module="${module//\//.}"
        while IFS= read -r matched; do
          if [[ "$seen" != *"|$matched|"* ]]; then seen="${seen}${matched}|"; printf '%s\n' "$matched"; fi
        done < <(grep -l -e "$module" -e "${module%.*}" "$repository_root/$api_root"/tests/test_*.py 2>/dev/null || true)
        ;;
      "$source_root"/app/contexts/acquisition/*.py|"$source_root"/app/infrastructure/acquisition.py|"$source_root"/app/infrastructure/crawler_gateway.py|"$source_root"/app/api/v1/acquisition.py|"$source_root"/app/api/dependencies/acquisition.py|"$source_root"/app/models/acquisition_job.py)
        for matched in tests/test_acquisition_domain.py tests/test_acquisition_application.py tests/test_acquisition_http_gateway.py tests/test_acquisition_api.py tests/test_acquisition_offline_e2e.py; do
          if [[ "$seen" != *"|$matched|"* ]]; then seen="${seen}${matched}|"; printf '%s\n' "$matched"; fi
        done
        ;;
    esac
  done < <(changed_files "$base_sha")
}

main_classifier() {
  local base_sha="${1:-${MODULE_BASE_SHA:-}}"
  if [ "${MODULE_FULL:-true}" != "true" ] && [ -n "$base_sha" ] && ! main_requires_full "$base_sha"; then
    printf 'mode=affected\n'
    main_affected_tests "$base_sha"
  else
    printf 'mode=full\n'
  fi
}

run_main() {
  local pytest_args=(-n 4 --dist loadfile --basetemp "$pytest_basetemp")
  local base_sha="${MODULE_BASE_SHA:-}"
  local mode="full"
  if [ "${MODULE_FULL:-true}" != "true" ] && [ -n "$base_sha" ] && ! main_requires_full "$base_sha"; then
    mode="affected"
    local affected=("${main_core_guardrails[@]}")
    local file
    while IFS= read -r file; do affected+=("$file"); done < <(main_affected_tests "$base_sha")
    local seen="|"
    local deduped=()
    for file in "${affected[@]}"; do
      if [[ "$seen" != *"|$file|"* ]]; then seen="${seen}${file}|"; deduped+=("$file"); fi
    done
    pytest_args+=("${deduped[@]}")
    printf '=== Main backend mode: affected (%d test files) ===\n' "${#deduped[@]}"
  else
    printf '=== Main backend mode: full ===\n'
  fi
  pytest_args+=("${coverage_args[@]}")
  run_module "Main backend" "$(module_path Main)" python -m pytest "${pytest_args[@]}"
}

frontend_test_mode="none"
frontend_test_files=()

frontend_select_tests() {
  local base_sha="$1"
  local frontend_path="$2"
  local frontend_root="$repository_root/$frontend_path"
  local file rel candidate feature_dir feature_name selected_before
  local full=0
  local seen="|"
  frontend_test_mode="none"
  frontend_test_files=()

  add_frontend_test() {
    local test_file="$1"
    if [[ "$seen" != *"|$test_file|"* ]]; then
      seen="${seen}${test_file}|"
      frontend_test_files+=("$test_file")
    fi
  }

  while IFS= read -r file; do
    case "$file" in
      "$frontend_path"/*) rel="${file#"$frontend_path"/}" ;;
      *) continue ;;
    esac

    case "$rel" in
      package.json|package-lock.json|vite.config.*|tsconfig*.json|src/testSetup.ts|src/main.tsx|src/App.tsx|src/app/*)
        full=1
        continue
        ;;
      src/*.test.ts|src/*.test.tsx|src/**/*.test.ts|src/**/*.test.tsx)
        add_frontend_test "$rel"
        continue
        ;;
    esac

    case "$rel" in
      src/*.ts|src/*.tsx)
        selected_before="${#frontend_test_files[@]}"
        candidate="${rel%.tsx}.test.tsx"
        if [ "${rel##*.}" = "ts" ]; then
          candidate="${rel%.ts}.test.ts"
        fi
        if [ -f "$frontend_root/$candidate" ]; then
          add_frontend_test "$candidate"
        else
          candidate="${rel%.*}.test.tsx"
          if [ -f "$frontend_root/$candidate" ]; then
            add_frontend_test "$candidate"
          else
            candidate="${rel%.*}.test.ts"
            if [ -f "$frontend_root/$candidate" ]; then
              add_frontend_test "$candidate"
            fi
          fi
        fi
        if [ "${#frontend_test_files[@]}" -eq "$selected_before" ] && \
          [[ "$rel" == src/features/*/* ]]; then
          feature_dir="${rel#src/features/}"
          feature_name="${feature_dir%%/*}"
          while IFS= read -r candidate; do
            candidate="${candidate#"$frontend_root"/}"
            add_frontend_test "$candidate"
          done < <(find "$frontend_root/src/features/$feature_name" -type f \( -name '*.test.ts' -o -name '*.test.tsx' \) -print 2>/dev/null | sort)
        fi
        if [[ "$rel" == src/api.ts || "$rel" == src/shared/* ]] && \
          [ "${#frontend_test_files[@]}" -eq "$selected_before" ]; then
          full=1
        fi
        ;;
    esac
  done < <(changed_files "$base_sha")

  if (( full != 0 )); then
    frontend_test_mode="full"
  elif ((${#frontend_test_files[@]} != 0)); then
    frontend_test_mode="targeted"
  fi
}

run_knowledge_graph() {
  local pytest_args=(-n 4 --dist loadfile --basetemp "$pytest_basetemp")
  if [ "${MODULE_FULL:-true}" != "true" ]; then
    # PR CI skips the branch-coverage gate; final-integration enforces it in full mode.
    pytest_args+=(--no-cov)
  fi
  pytest_args+=("${coverage_args[@]}")
  run_module "Knowledge Graph" "$(module_path KnowledgeGraph)" \
    python -m pytest "${pytest_args[@]}" || return $?
}

run_trend_intelligence() {
  local pytest_args=(--basetemp "$pytest_basetemp")
  pytest_args+=("${coverage_args[@]}")
  run_module "Trend Intelligence" "$(module_path TrendIntelligence)" \
    python -m pytest "${pytest_args[@]}" || return $?
}

run_embedding_service() {
  local pytest_args=(tests/test_api.py --basetemp "$pytest_basetemp")
  pytest_args+=("${coverage_args[@]}")
  run_module "Embedding Service" "$(module_path EmbeddingService)" \
    python -m pytest "${pytest_args[@]}" || return $?
  run_module "Embedding Service config" "$(module_path EmbeddingService)" \
    python -c "from app.config import Settings; Settings()" || return $?
}

run_frontend() {
  local frontend
  frontend="$(module_path Frontend)"
  run_module "Main frontend lint" "$frontend" npm run lint || return $?
  if [ "${MODULE_FULL:-true}" = "true" ] || [ -z "${MODULE_BASE_SHA:-}" ]; then
    run_module "Main frontend" "$frontend" npm test || return $?
  else
    frontend_select_tests "$MODULE_BASE_SHA" "$frontend"
    case "$frontend_test_mode" in
      full)
        printf '=== Frontend PR test mode: full ===\n'
        run_module "Main frontend (full)" "$frontend" npm test || return $?
        ;;
      targeted)
        printf '=== Frontend PR test mode: targeted (%d test files) ===\n' "${#frontend_test_files[@]}"
        run_module "Main frontend (targeted)" "$frontend" npm test -- "${frontend_test_files[@]}" || return $?
        ;;
      none)
        printf '=== Frontend PR test mode: none (no relevant unit tests) ===\n'
        ;;
    esac
  fi
  run_module "Main frontend typecheck" "$frontend" npm run typecheck || return $?
  run_module "Main frontend build" "$frontend" npm run build:ci || return $?
}

# The classifier-only path is used by the short shell routing tests. It exits
# before any module command, so it never runs pytest/npm or touches a database.
if [ "${TEST_ALL_CLASSIFIER_ONLY:-0}" = "1" ]; then
  case "$suite" in
    Main) main_classifier "${MODULE_BASE_SHA:-}" ;;
    Frontend)
      frontend_select_tests "${MODULE_BASE_SHA:-}" "$(module_path Frontend)"
      printf 'mode=%s\n' "$frontend_test_mode"
      if ((${#frontend_test_files[@]} != 0)); then
        printf '%s\n' "${frontend_test_files[@]}"
      fi
      ;;
    *)
      printf 'Classifier-only mode supports Main and Frontend: %s\n' "$suite" >&2
      exit 2
      ;;
  esac
  classifier_status=$?
  if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return "$classifier_status"
  fi
  exit "$classifier_status"
fi

configure_coverage "$suite"

case "$suite" in
  Main) run_main || exit $? ;;
  KnowledgeGraph)
    run_knowledge_graph || exit $?
    ;;
  MatchingService)
    pytest_args=(--basetemp "$pytest_basetemp")
    pytest_args+=("${coverage_args[@]}")
    run_module "Matching Service" "$(module_path MatchingService)" \
      python -m pytest "${pytest_args[@]}"
    matching_status=$?
    # Keep the pytest-cov exit status as the shell status.  In particular, a
    # pytest-cov fail-under result is a failed CI job even when all test cases
    # themselves passed.
    if (( matching_status != 0 )); then
      printf 'Matching Service coverage/test failure propagated (exit %d)\n' "$matching_status" >&2
      exit "$matching_status"
    fi
    ;;
  EmergingDiscovery)
    pytest_args=(--basetemp "$pytest_basetemp")
    pytest_args+=("${coverage_args[@]}")
    run_module "Emerging Discovery" "$(module_path EmergingDiscovery)" \
      python -m pytest "${pytest_args[@]}" || exit $?
    ;;
  TrendIntelligence)
    run_trend_intelligence || exit $?
    ;;
  EmbeddingService)
    run_embedding_service || exit $?
    ;;
  Crawler)
    # Repository-level architecture/contract/integration ownership lives in
    # tests and is invoked by tests/run-ci.sh. The two small crawler
    # regression tests and the multi-company suite use deterministic browser
    # fakes. Include both because Playwright and Liepin are active production
    # schedule actions; no test in this gate contacts a recruitment website.
    pytest_args=(tests unified_api/tests multi_company_scraper/tests --basetemp "$pytest_basetemp")
    pytest_args+=("${coverage_args[@]}")
    run_module "Crawler" "$(module_path Crawler)" \
      python -m pytest "${pytest_args[@]}" || exit $?
    ;;
  JDExtraction)
    pytest_args=(-n 4 --dist loadfile --basetemp "$pytest_basetemp")
    pytest_args+=("${coverage_args[@]}")
    run_module "JD Extraction" "$(module_path JDExtraction)" \
      python -m pytest "${pytest_args[@]}" || exit $?
    ;;
  CVExtraction)
    pytest_args=(--basetemp "$pytest_basetemp")
    pytest_args+=("${coverage_args[@]}")
    run_module "CV Extraction" "$(module_path CVExtraction)" \
      python -m pytest "${pytest_args[@]}" || exit $?
    ;;
  Frontend)
    run_frontend || exit $?
    ;;
  *)
    printf 'Unknown suite: %s\n' "$suite" >&2
    printf 'Allowed suites: Main KnowledgeGraph MatchingService EmergingDiscovery TrendIntelligence EmbeddingService Crawler JDExtraction CVExtraction Frontend\n' >&2
    exit 2
    ;;
esac

printf '\n=== Test summary: PASSED ===\n'
printf 'Completed modules: %s\n' "${completed[*]}"
