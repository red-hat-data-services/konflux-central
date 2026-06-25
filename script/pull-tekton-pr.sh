#!/usr/bin/env bash
set -euo pipefail

# CI helper — creates a branch, commits .tekton/ changes, and opens a PR.
# Intended to be called after pull-tekton.sh. Requires env vars:
#   REPOSITORY    — repo name (e.g. odh-dashboard)
#   REF           — ref that was pulled from
#   BASE_BRANCH   — branch to PR against
#   GITHUB_ORG    — org name (default: red-hat-data-services)
#   CI_JOB_URL    — link to CI run (optional)
#
# For local use, just run pull-tekton.sh and commit manually.

: "${REPOSITORY:?REPOSITORY is required}"
: "${REF:?REF is required}"
: "${BASE_BRANCH:?BASE_BRANCH is required}"
: "${GITHUB_ORG:=red-hat-data-services}"
: "${CI_JOB_URL:=}"

BRANCH="pull-tekton/${REPOSITORY}-$(date +%s)"
git checkout -b "$BRANCH"
git add "pipelineruns/${REPOSITORY}/.tekton"

if git diff --staged --quiet; then
  echo "No changes detected — .tekton/ is already up to date."
  exit 0
fi

git commit -m "pull .tekton/ from ${REPOSITORY} @ ${REF}"
git push origin "$BRANCH"

BODY="Pulled \`.tekton/\` from \`${GITHUB_ORG}/${REPOSITORY}\` at ref \`${REF}\` into \`pipelineruns/${REPOSITORY}/.tekton/\`.

Only files already tracked in this repo were updated."

if [[ -n "$CI_JOB_URL" ]]; then
  BODY="${BODY}

Triggered by: ${CI_JOB_URL}"
fi

gh pr create \
  --base "$BASE_BRANCH" \
  --head "$BRANCH" \
  --title "Pull .tekton/ from ${REPOSITORY} @ ${REF}" \
  --body "$BODY"
