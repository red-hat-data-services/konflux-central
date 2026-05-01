#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [ORG/]REPO REF"
  echo
  echo "Pull .tekton/ from a remote repo into pipelineruns/<REPO>/.tekton/"
  echo
  echo "Arguments:"
  echo "  [ORG/]REPO  GitHub repo, optionally with org (default: red-hat-data-services)"
  echo "  REF         Branch, tag, or commit SHA to pull from"
  exit 1
}

[[ $# -eq 2 ]] || usage

ORG_REPO="$1"
REF="$2"

if [[ "$ORG_REPO" != */* ]]; then
  ORG_REPO="red-hat-data-services/$ORG_REPO"
fi
REPO="${ORG_REPO#*/}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${KONFLUX_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TARGET_DIR="$ROOT_DIR/pipelineruns/$REPO/.tekton"

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: $TARGET_DIR does not exist" >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Fetching .tekton/ from $ORG_REPO @ $REF ..."
git init "$TMPDIR/repo" -q
git -C "$TMPDIR/repo" remote add origin "https://github.com/$ORG_REPO.git"
git -C "$TMPDIR/repo" sparse-checkout set .tekton
git -C "$TMPDIR/repo" fetch --depth 1 origin "$REF" 2>&1
git -C "$TMPDIR/repo" checkout FETCH_HEAD -q

if [[ ! -d "$TMPDIR/repo/.tekton" ]]; then
  echo "Error: .tekton/ not found in $ORG_REPO @ $REF" >&2
  exit 1
fi

UPDATED=0
for f in "$TARGET_DIR"/*; do
  BASENAME="$(basename "$f")"
  if [[ -f "$TMPDIR/repo/.tekton/$BASENAME" ]]; then
    cp "$TMPDIR/repo/.tekton/$BASENAME" "$TARGET_DIR/$BASENAME"
    echo "  updated: $BASENAME"
    UPDATED=$((UPDATED + 1))
  else
    echo "  skipped: $BASENAME (not in remote .tekton/)"
  fi
done

echo "Updated $UPDATED file(s) in $TARGET_DIR from $ORG_REPO @ $REF"
echo
echo "Review the changes with: git diff pipelineruns/$REPO/.tekton"
