#!/usr/bin/env bash
#
# Run Renovate against a single repository using the mintmaker image.
#
# Usage:
#   RENOVATE_TOKEN=<github-token> ./script/run-renovate.sh \
#     --repo <org/repo> \
#     --config-file <path/to/renovate-config.json> \
#     [--image <renovate-image>] \
#     [--branches '["main","rhoai-3.4"]'] \
#     [--dry-run] \
#     [--log-level debug]
#
# Outputs (when GITHUB_STEP_SUMMARY is set):
#   - Repo/config/branch info
#   - PR links or dry-run results

set -euo pipefail

# Defaults
DRY_RUN=false
LOG_LEVEL=debug
LOG_FORMAT=""
BRANCHES_JSON="[]"
IMAGE="quay.io/konflux-ci/mintmaker-renovate-image:latest"
NO_PULL=false

usage() {
    echo "Usage: RENOVATE_TOKEN=<token> $0 --repo ORG/REPO --config-file PATH [options]"
    echo ""
    echo "Environment:"
    echo "  RENOVATE_TOKEN   GitHub token for Renovate (required)"
    echo ""
    echo "Required:"
    echo "  --repo           Target repository (e.g. red-hat-data-services/odh-dashboard)"
    echo "  --config-file    Path to the Renovate config file to use"
    echo ""
    echo "Optional:"
    echo "  --image          Renovate container image (default: $IMAGE)"
    echo "  --branches       JSON array of base branches (default: use config's baseBranches)"
    echo "  --dry-run        Run in dry-run mode (no PRs created)"
    echo "  --log-level      Renovate log level: debug, info, warn (default: debug)"
    echo ""
    echo "Environment (optional):"
    echo "  RENOVATE_HOST_RULES  JSON array of hostRules for container registry auth"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --repo)         REPO="$2"; shift 2 ;;
        --config-file)  CONFIG_FILE="$2"; shift 2 ;;
        --image)        IMAGE="$2"; shift 2 ;;
        --branches)     BRANCHES_JSON="$2"; shift 2 ;;
        --dry-run)      DRY_RUN=true; shift ;;
        --log-level)    LOG_LEVEL="$2"; shift 2 ;;
        --log-format)   LOG_FORMAT="$2"; shift 2 ;;
        --no-pull)      NO_PULL=true; shift ;;
        -h|--help)      usage ;;
        *)              echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "${RENOVATE_TOKEN:-}" ]]; then
    echo "error: RENOVATE_TOKEN environment variable is required" >&2
    usage
fi

if [[ -z "${REPO:-}" ]]; then
    echo "error: --repo is required" >&2
    usage
fi

# Match production: MintMaker merges renovate.json + self_hosted.json as
# its base config. We use RENOVATE_CONFIG_FILE for renovate.json and
# RENOVATE_ADDITIONAL_CONFIG_FILE for self_hosted.json (additional overrides primary).
MINTMAKER_BASE_URL="https://raw.githubusercontent.com/konflux-ci/mintmaker/main/config/renovate"

MINTMAKER_BASE=$(mktemp)
curl -sL "$MINTMAKER_BASE_URL/renovate.json" > "$MINTMAKER_BASE"
chmod 644 "$MINTMAKER_BASE"

# Only pass the self-hosted keys we need (Redis, caching, etc. are
# cluster-specific and not available in CI).
MINTMAKER_SELF_HOSTED=$(mktemp)
echo '{"allowShellExecutorForPostUpgradeCommands": true}' > "$MINTMAKER_SELF_HOSTED"
chmod 644 "$MINTMAKER_SELF_HOSTED"

# Force is only used to override baseBranchPatterns when --branches is specified.
FORCE_CONFIG='{}'
if [[ "$BRANCHES_JSON" != "[]" ]]; then
    FORCE_CONFIG=$(python3 -c "
import json, sys
print(json.dumps({'baseBranchPatterns': json.loads(sys.argv[1])}))
" "$BRANCHES_JSON")
fi

# Build docker flags
docker_flags=()
docker_flags+=(-e "RENOVATE_TOKEN=$RENOVATE_TOKEN")
docker_flags+=(-e "RENOVATE_REPOSITORIES=[\"$REPO\"]")
docker_flags+=(-e "RENOVATE_CONFIG_FILE=/tmp/mintmaker-base.json")
docker_flags+=(-e "RENOVATE_ADDITIONAL_CONFIG_FILE=/tmp/mintmaker-self-hosted.json")
docker_flags+=(-e "RENOVATE_FORCE=$FORCE_CONFIG")
docker_flags+=(-e "LOG_LEVEL=$LOG_LEVEL")
docker_flags+=(-e "RENOVATE_PR_HOURLY_LIMIT=20")
docker_flags+=(-e "RENOVATE_BRANCH_CONCURRENT_LIMIT=20")
docker_flags+=(-e "RENOVATE_RECREATE_WHEN=always")
docker_flags+=(-e "RENOVATE_DEPENDENCY_DASHBOARD=false")

if [[ -n "$LOG_FORMAT" ]]; then
    docker_flags+=(-e "LOG_FORMAT=$LOG_FORMAT")
fi

if [[ "$DRY_RUN" == "true" ]]; then
    docker_flags+=(-e "RENOVATE_DRY_RUN=full")
fi

docker_flags+=(-v "$MINTMAKER_BASE:/tmp/mintmaker-base.json:ro")
docker_flags+=(-v "$MINTMAKER_SELF_HOSTED:/tmp/mintmaker-self-hosted.json:ro")

if [[ -n "${RENOVATE_HOST_RULES:-}" ]]; then
    docker_flags+=(-e "RENOVATE_HOST_RULES=$RENOVATE_HOST_RULES")
fi

pull_policy="missing"
if [[ "$NO_PULL" == "true" ]]; then
    pull_policy="never"
fi

podman run --rm --pull="$pull_policy" "${docker_flags[@]}" "$IMAGE" renovate
