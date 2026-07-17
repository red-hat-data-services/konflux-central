#!/usr/bin/env bash
#
# Run Renovate in local platform mode for dry-run testing.
#
# Uses --platform=local so Renovate reads files from the current checkout
# instead of cloning from GitHub. This allows testing config changes on
# feature branches before merging.
#
# Config layering matches production:
#   - MintMaker's config as RENOVATE_CONFIG_FILE (base, lower priority)
#   - Our config mounted as .github/renovate.json (repo config, overrides base)
#
# Usage:
#   RENOVATE_TOKEN=<token> ./script/run-renovate-local.sh \
#     --config-file renovate/pipelines-renovate.json5 \
#     [--image <renovate-image>]
#
# For local dev with ARM Mac:
#   RENOVATE_TOKEN=$(gh auth token) ./script/run-renovate-local.sh \
#     --config-file renovate/pipelines-renovate.json5 \
#     --image localhost/mintmaker-renovate:local

set -euo pipefail

# Defaults
IMAGE="quay.io/konflux-ci/mintmaker-renovate-image:latest"
LOG_LEVEL=debug
LOG_FORMAT=json
NO_PULL=false

MINTMAKER_BASE_URL="https://raw.githubusercontent.com/konflux-ci/mintmaker/main/config/renovate"

usage() {
    echo "Usage: RENOVATE_TOKEN=<token> $0 --config-file PATH [options]"
    echo ""
    echo "Environment:"
    echo "  RENOVATE_TOKEN       GitHub token (required for version lookups)"
    echo "  RENOVATE_HOST_RULES  JSON array of hostRules for registry auth"
    echo ""
    echo "Required:"
    echo "  --config-file    Path to the Renovate config file (e.g. renovate/pipelines-renovate.json5)"
    echo ""
    echo "Optional:"
    echo "  --image          Renovate container image (default: $IMAGE)"
    echo "  --no-pull        Don't pull the image (use local build)"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --config-file)  CONFIG_FILE="$2"; shift 2 ;;
        --image)        IMAGE="$2"; shift 2 ;;
        --no-pull)      NO_PULL=true; shift ;;
        -h|--help)      usage ;;
        *)              echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "${RENOVATE_TOKEN:-}" ]]; then
    echo "error: RENOVATE_TOKEN environment variable is required" >&2
    usage
fi

if [[ -z "${CONFIG_FILE:-}" ]]; then
    echo "error: --config-file is required" >&2
    usage
fi

CONFIG_FILE=$(realpath "$CONFIG_FILE")

# Download MintMaker's configs (renovate.json + self_hosted.json)
MINTMAKER_BASE=$(mktemp)
curl -sL "$MINTMAKER_BASE_URL/renovate.json" > "$MINTMAKER_BASE"
chmod 644 "$MINTMAKER_BASE"

MINTMAKER_SELF_HOSTED=$(mktemp)
curl -sL "$MINTMAKER_BASE_URL/self_hosted.json" > "$MINTMAKER_SELF_HOSTED"
chmod 644 "$MINTMAKER_SELF_HOSTED"

# Convert JSON5 config to JSON for mounting as repo config.
# Try bare python3 first (CI has json5 installed); fall back to uv for local dev.
REPO_CONFIG=$(mktemp)
if python3 -c "import json5" 2>/dev/null; then
    PYTHON=python3
else
    PYTHON="uv run --with json5 python3"
fi
$PYTHON -c "
import json, json5, sys
with open(sys.argv[1]) as f:
    config = json5.loads(f.read())
json.dump(config, sys.stdout, indent=2)
" "$CONFIG_FILE" > "$REPO_CONFIG"
chmod 644 "$REPO_CONFIG"

# Build docker flags
docker_flags=()
docker_flags+=(-e "RENOVATE_TOKEN=$RENOVATE_TOKEN")
docker_flags+=(-e "RENOVATE_CONFIG_FILE=/tmp/mintmaker-base.json")
docker_flags+=(-e "RENOVATE_ADDITIONAL_CONFIG_FILE=/tmp/mintmaker-self-hosted.json")
docker_flags+=(-e "LOG_LEVEL=$LOG_LEVEL")
docker_flags+=(-e "LOG_FORMAT=$LOG_FORMAT")

if [[ -n "${RENOVATE_HOST_RULES:-}" ]]; then
    docker_flags+=(-e "RENOVATE_HOST_RULES=$RENOVATE_HOST_RULES")
fi

# Mount workspace and config files
docker_flags+=(-v "$(pwd):/workspace:ro")
docker_flags+=(-v "$REPO_CONFIG:/workspace/.github/renovate.json:ro")
docker_flags+=(-v "$MINTMAKER_BASE:/tmp/mintmaker-base.json:ro")
docker_flags+=(-v "$MINTMAKER_SELF_HOSTED:/tmp/mintmaker-self-hosted.json:ro")
docker_flags+=(-w /workspace)

pull_policy="missing"
if [[ "$NO_PULL" == "true" ]]; then
    pull_policy="never"
fi

podman run --rm --pull="$pull_policy" "${docker_flags[@]}" "$IMAGE" \
    renovate --platform=local --dry-run=lookup
