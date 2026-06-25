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

if [[ -z "${REPO:-}" || -z "${CONFIG_FILE:-}" ]]; then
    echo "error: --repo and --config-file are required" >&2
    usage
fi

CONFIG_FILE=$(realpath "$CONFIG_FILE")

# Our source config is RENOVATE_CONFIG_FILE (mounted into the container).
# MintMaker's global config is the base via RENOVATE_EXTENDS.
# RENOVATE_FORCE overrides only enabledManagers (MintMaker's extends sets
# ~60 managers) and baseBranchPatterns (when --branches is provided).
MINTMAKER_EXTENDS="github>konflux-ci/mintmaker//config/renovate/renovate.json"

# Build force config with only the fields that need overriding.
FORCE_CONFIG=$(python3 -c "
import json, json5, sys
with open(sys.argv[1]) as f:
    config = json5.loads(f.read())
force = {}
if 'enabledManagers' in config:
    force['enabledManagers'] = config['enabledManagers']
branches = sys.argv[2]
if branches != '[]':
    force['baseBranchPatterns'] = json.loads(branches)
json.dump(force, sys.stdout)
" "$CONFIG_FILE" "$BRANCHES_JSON")

# Build docker flags
docker_flags=()
docker_flags+=(-e "RENOVATE_TOKEN=$RENOVATE_TOKEN")
docker_flags+=(-e "RENOVATE_REPOSITORIES=[\"$REPO\"]")
docker_flags+=(-e "RENOVATE_REQUIRE_CONFIG=ignored")
docker_flags+=(-e "RENOVATE_CONFIG_FILE=/tmp/renovate-config.json")
docker_flags+=(-e "RENOVATE_EXTENDS=[\"$MINTMAKER_EXTENDS\"]")
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

docker_flags+=(-v "$CONFIG_FILE:/tmp/renovate-config.json:ro")

pull_policy="missing"
if [[ "$NO_PULL" == "true" ]]; then
    pull_policy="never"
fi

podman run --rm --pull="$pull_policy" "${docker_flags[@]}" "$IMAGE" renovate
