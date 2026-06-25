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
BRANCHES_JSON="[]"
IMAGE="quay.io/konflux-ci/mintmaker-renovate-image:latest"

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
SHORT_NAME=$(basename "$REPO")

# Write step summary header if running in GitHub Actions
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo "### $SHORT_NAME"
        echo ""
        echo "- **Repo:** \`$REPO\`"
        echo "- **Config:** \`$CONFIG_FILE\`"
        echo "- **Branches:** $BRANCHES_JSON"
    } >> "$GITHUB_STEP_SUMMARY"
fi

# Build docker flags
docker_flags=()
docker_flags+=(-e "RENOVATE_TOKEN=$RENOVATE_TOKEN")
docker_flags+=(-e "RENOVATE_REPOSITORIES=[\"$REPO\"]")
docker_flags+=(-e "RENOVATE_REQUIRE_CONFIG=ignored")
docker_flags+=(-e "RENOVATE_CONFIG_FILE=/tmp/renovate-config.json")
docker_flags+=(-e "LOG_LEVEL=$LOG_LEVEL")
docker_flags+=(-e "RENOVATE_PR_HOURLY_LIMIT=20")
docker_flags+=(-e "RENOVATE_BRANCH_CONCURRENT_LIMIT=20")

if [[ "$DRY_RUN" == "true" ]]; then
    docker_flags+=(-e "RENOVATE_DRY_RUN=full")
fi

if [[ "$BRANCHES_JSON" != "[]" && -n "$BRANCHES_JSON" ]]; then
    docker_flags+=(-e "RENOVATE_BASE_BRANCHES=$BRANCHES_JSON")
fi

docker_flags+=(-v "$CONFIG_FILE:/tmp/renovate-config.json:ro")

# Run Renovate
set +e
podman run --rm --platform linux/amd64 "${docker_flags[@]}" "$IMAGE" renovate 2>&1 | tee /tmp/renovate.log
exit_code=${PIPESTATUS[0]}
set -e

# Extract PR URLs from the log
pr_urls=$(grep -oE 'https://github\.com/[^ "]*pull/[0-9]+' /tmp/renovate.log | sort -u || true)

# Write results to step summary
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo ""
        if [[ -n "$pr_urls" ]]; then
            echo "#### PRs Created"
            echo ""
            while IFS= read -r url; do
                echo "- $url"
            done <<< "$pr_urls"
        elif [[ "$DRY_RUN" == "true" ]]; then
            echo "#### Dry Run Complete"
            echo ""
            echo "No PRs were created (dry run mode)."
            dry_run_prs=$(grep -E 'DRY-RUN.*Would (create|update)' /tmp/renovate.log || true)
            if [[ -n "$dry_run_prs" ]]; then
                echo ""
                echo '```'
                echo "$dry_run_prs"
                echo '```'
            fi
        else
            echo "#### No PRs Created"
            echo ""
            echo "Renovate did not create any PRs. Check the logs for details."
        fi
    } >> "$GITHUB_STEP_SUMMARY"
else
    # Standalone mode — print results to stdout
    if [[ -n "$pr_urls" ]]; then
        echo ""
        echo "PRs created:"
        echo "$pr_urls"
    elif [[ "$DRY_RUN" == "true" ]]; then
        echo ""
        echo "Dry run complete. No PRs created."
        dry_run_prs=$(grep -E 'DRY-RUN.*Would (create|update)' /tmp/renovate.log || true)
        if [[ -n "$dry_run_prs" ]]; then
            echo "$dry_run_prs"
        fi
    fi
fi

exit $exit_code
