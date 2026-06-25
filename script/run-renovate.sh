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
CONFIG_REF=""

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
    echo "  --config-ref     Git ref (SHA/branch) for the config file (default: HEAD)"
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
        --config-ref)   CONFIG_REF="$2"; shift 2 ;;
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

WRAPPER_CONFIG=$(mktemp /tmp/renovate-wrapper-XXXXX.json)
trap 'rm -f "$WRAPPER_CONFIG"' EXIT

if [[ -n "$CONFIG_REF" ]]; then
    # Config is pushed to GitHub — build a pure-extends wrapper that layers
    # MintMaker's global config first, then our source config second. Our
    # config comes last so its unmergeable fields (like enabledManagers)
    # replace MintMaker's values.
    REPO_ROOT=$(git rev-parse --show-toplevel)
    CONFIG_REL_PATH=$(realpath --relative-to="$REPO_ROOT" "$CONFIG_FILE")
    CONFIG_REPO=$(git remote get-url origin | sed -E 's|.*github\.com[:/]||; s|\.git$||')
    SOURCE_EXTENDS="github>${CONFIG_REPO}//${CONFIG_REL_PATH}#${CONFIG_REF}"
    MINTMAKER_EXTENDS="github>konflux-ci/mintmaker//config/renovate/renovate.json"

    python3 -c "
import json, sys
json.dump({'extends': [sys.argv[1], sys.argv[2]]}, sys.stdout, indent=2)
" "$MINTMAKER_EXTENDS" "$SOURCE_EXTENDS" > "$WRAPPER_CONFIG"
else
    # No ref — use the local config file directly (no MintMaker layering).
    cp "$CONFIG_FILE" "$WRAPPER_CONFIG"
fi

chmod 644 "$WRAPPER_CONFIG"

# Build docker flags
docker_flags=()
docker_flags+=(-e "RENOVATE_TOKEN=$RENOVATE_TOKEN")
docker_flags+=(-e "RENOVATE_REPOSITORIES=[\"$REPO\"]")
docker_flags+=(-e "RENOVATE_REQUIRE_CONFIG=ignored")
docker_flags+=(-e "RENOVATE_CONFIG_FILE=/tmp/renovate-config.json")
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

if [[ "$BRANCHES_JSON" != "[]" && -n "$BRANCHES_JSON" ]]; then
    docker_flags+=(-e "RENOVATE_BASE_BRANCHES=$BRANCHES_JSON")
fi

docker_flags+=(-v "$WRAPPER_CONFIG:/tmp/renovate-config.json:ro")

pull_policy="missing"
if [[ "$NO_PULL" == "true" ]]; then
    pull_policy="never"
fi

podman run --rm --pull="$pull_policy" --platform linux/amd64 "${docker_flags[@]}" "$IMAGE" renovate
