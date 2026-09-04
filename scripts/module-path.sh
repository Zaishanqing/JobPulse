#!/usr/bin/env bash
# Single source of truth for module owner paths.
#
# All produced paths are RELATIVE TO THE REPOSITORY ROOT:
#   resolve_module_path <suite> [tree]  ->  apps/api | services/<service>
set -uo pipefail

JOBPULSE_ROOT="${JOBPULSE_ROOT:-JobPulse}"

module_paths() {
  case "${1:-}" in
    Main) printf '%s' "apps/api" ;;
    Crawler) printf '%s' "services/crawler" ;;
    JDExtraction) printf '%s' "services/jd-extraction" ;;
    CVExtraction) printf '%s' "services/cv-extraction" ;;
    KnowledgeGraph) printf '%s' "services/knowledge-graph" ;;
    MatchingService) printf '%s' "services/matching-service" ;;
    EmergingDiscovery) printf '%s' "services/emerging-discovery" ;;
    TrendIntelligence) printf '%s' "services/trend-intelligence" ;;
    EmbeddingService) printf '%s' "services/embedding-service" ;;
    Frontend) printf '%s' "apps/web" ;;
    *) return 1 ;;
  esac
}

resolve_module_path() {
  local suite_name="$1"
  local tree="$2"
  local path
  path="$(module_paths "$suite_name")" || return 2
  if [ "$tree" = "$JOBPULSE_ROOT" ]; then
    printf '%s' "$path"
  else
    printf 'unknown module tree: %s\n' "$tree" >&2
    return 3
  fi
}

# CLI dispatch (only when executed directly; sourcing stays side-effect free).
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    resolve)
      resolve_module_path "${2:?usage: module-path.sh resolve <suite> [tree]}" "${3:-$JOBPULSE_ROOT}"
      ;;
    list)
      module_paths "${2:?usage: module-path.sh list <suite>}"
      ;;
    *)
      printf 'usage: module-path.sh resolve <suite> [tree] | list <suite>\n' >&2
      exit 2
      ;;
  esac
fi
