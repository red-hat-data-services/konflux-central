#!/bin/bash

# ==============================================================================
# RHOAI Pipelinerun Manager
# ==============================================================================
# This script creates/updates Tekton pipelineruns for RHOAI releases in two modes:
#
#   CREATE MODE:
#   ────────────
#   Creates pipelineruns for a new minor version by using existing pipelineruns
#   from a source branch as a template. Updates all version references and
#   renames files accordingly.
#
#   - Only used for the FIRST EA release of a new minor version (X.Y.0-ea.1)
#   - Example: Use rhoai-3.3 pipelineruns to create rhoai-3.4 pipelineruns
#              for the first EA release (3.4.0-ea.1)
#
#   UPDATE MODE:
#   ────────────
#   Updates the rhoai-version param in existing pipelineruns within the target
#   branch. Used for all subsequent releases after the first EA.
#
#   Scenarios:
#   - Second EA release:    3.4.0-ea.1 -> 3.4.0-ea.2
#   - Third EA release:     3.4.0-ea.2 -> 3.4.0-ea.3
#   - EA hotfix:            3.4.0-ea.1 -> 3.4.0-ea.1.1
#   - GA release:           3.4.0-ea.2 -> 3.4.0
#   - Z-stream release:     3.4.0 -> 3.4.1
#   - Second z-stream:      3.4.1 -> 3.4.2
#
# Supported version formats (X=single digit 0-9, Y/Z=1-2 digits 0-99):
#   - GA:         X.Y.Z          (e.g., 3.4.0, 3.14.1, 4.0.2)
#   - EA:         X.Y.Z-ea.N     (e.g., 3.4.0-ea.1, 3.14.0-ea.2)
#   - EA Hotfix:  X.Y.Z-ea.N.H   (e.g., 3.4.0-ea.1.1, 3.14.0-ea.1.2)
# ==============================================================================

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source utility functions
source "${SCRIPT_DIR}/rhoai_utils.sh"

# Mode of operation: "create" or "update"
MODE=""

# Source branch for create mode (--from)
FROM_BRANCH=""

# Target branch (--target)
TARGET_BRANCH=""

# Full RHOAI version string (e.g., "3.4.0", "3.4.0-ea.1", "3.4.0-ea.1.1")
RHOAI_VERSION=""

# Directory containing Tekton pipelinerun YAML files (default: pipelineruns)
PIPELINERUNS_DIR="pipelineruns"

# Help message for script usage
usage() {
  echo "RHOAI Pipelinerun Manager"
  echo ""
  echo "Usage:"
  echo "  Create mode: $0 --mode create --rhoai-version <version> --target <target_branch> --from <source_branch> [--dir <dir>]"
  echo "  Update mode: $0 --mode update --rhoai-version <version> --target <target_branch> [--dir <dir>]"
  echo ""
  echo "Modes:"
  echo "  create   Creates pipelineruns for a new minor version by using existing pipelineruns as a template."
  echo "           Only used for the FIRST EA release of a new minor version (X.Y.0-ea.1)."
  echo "           The --from flag specifies the source branch to use as template."
  echo "           The --target flag specifies the target branch."
  echo ""
  echo "  update   Update the rhoai-version param in existing pipelineruns."
  echo "           Used for all subsequent releases after the first EA:"
  echo "           - Second+ EA releases (3.4.0-ea.2, 3.4.0-ea.3, ...)"
  echo "           - EA hotfixes (3.4.0-ea.1.1, 3.4.0-ea.1.2, ...)"
  echo "           - GA releases (3.4.0, 3.4.1, 3.4.2, ...)"
  echo "           The --target flag specifies the target branch."
  echo ""
  echo "Version Formats (X=single digit 0-9, Y/Z=1-2 digits 0-99):"
  echo "  GA release:   X.Y.Z         (e.g., 3.4.0, 3.14.1, 4.0.2)"
  echo "  EA release:   X.Y.Z-ea.N    (e.g., 3.4.0-ea.1, 3.14.0-ea.2)"
  echo "  EA hotfix:    X.Y.Z-ea.N.H  (e.g., 3.4.0-ea.1.1, 3.14.0-ea.1.2)"
  echo ""
  echo "Options:"
  echo "  -m, --mode <mode>                Mode of operation: 'create' or 'update' (required)"
  echo "  -v, --rhoai-version <version>    Target RHOAI version (required)"
  echo "  -t, --target <target_branch>     Target branch for the updated/created pipelineruns (required)"
  echo "  -f, --from <source_branch>       Source branch to use existing pipelineruns as a template (create mode only)"
  echo "  -d, --dir <pipelineruns_dir>     Directory containing pipelinerun YAML files (default: pipelineruns)"
  echo "  -h, --help                       Show this help message"
  echo ""
  echo "Examples:"
  echo ""
  echo "  CREATE MODE (First EA drop for a new minor version):"
  echo "  ─────────────────────────────────────────────────────────────────────────"
  echo "  # Create pipelineruns for 3.4.0-ea.1 using existing pipelineruns in the rhoai-3.3 branch as template"
  echo "  $0 --mode create --rhoai-version 3.4.0-ea.1 --target rhoai-3.4 --from rhoai-3.3"
  echo ""
  echo "  UPDATE MODE (All subsequent releases within same minor version):"
  echo "  ─────────────────────────────────────────────────────────────────────────"
  echo ""
  echo "  # Second EA release (3.4.0-ea.1 -> 3.4.0-ea.2)"
  echo "  $0 --mode update --rhoai-version 3.4.0-ea.2 --target rhoai-3.4"
  echo ""
  echo "  # Third EA release (3.4.0-ea.2 -> 3.4.0-ea.3)"
  echo "  $0 --mode update --rhoai-version 3.4.0-ea.3 --target rhoai-3.4"
  echo ""
  echo "  # EA hotfix for ea.1 (3.4.0-ea.1 -> 3.4.0-ea.1.1)"
  echo "  $0 --mode update --rhoai-version 3.4.0-ea.1.1 --target rhoai-3.4"
  echo ""
  echo "  # Second EA hotfix (3.4.0-ea.1.1 -> 3.4.0-ea.1.2)"
  echo "  $0 --mode update --rhoai-version 3.4.0-ea.1.2 --target rhoai-3.4"
  echo ""
  echo "  # GA release (3.4.0-ea.2 -> 3.4.0)"
  echo "  $0 --mode update --rhoai-version 3.4.0 --target rhoai-3.4"
  echo ""
  echo "  # Z-stream release (3.4.0 -> 3.4.1)"
  echo "  $0 --mode update --rhoai-version 3.4.1 --target rhoai-3.4"
  echo ""
  echo "  # Second z-stream release (3.4.1 -> 3.4.2)"
  echo "  $0 --mode update --rhoai-version 3.4.2 --target rhoai-3.4"
  exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -m|--mode)
      MODE="$2"
      shift 2
      ;;
    -f|--from)
      FROM_BRANCH="$2"
      shift 2
      ;;
    -t|--target)
      TARGET_BRANCH="$2"
      shift 2
      ;;
    -v|--rhoai-version)
      RHOAI_VERSION="$2"
      shift 2
      ;;
    -d|--dir)
      PIPELINERUNS_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Invalid option: $1" >&2
      usage
      ;;
  esac
done

# On macOS, use gsed (GNU sed) instead of BSD sed
if [[ "$(uname)" == "Darwin" ]]; then
  if ! command -v gsed &>/dev/null; then
    print_error "gsed is not installed. Please install it using 'brew install gnu-sed'."
    exit 1
  fi
  # Create a wrapper function so we can use 'sed' throughout the script
  sed() { gsed "$@"; }
fi

# Validate mode
if [[ -z "$MODE" ]]; then
  print_error "--mode is required."
  usage
fi

if [[ "$MODE" != "create" && "$MODE" != "update" ]]; then
  print_error "Invalid mode '$MODE'. Must be 'create' or 'update'."
  usage
fi

# Validate required arguments based on mode
if [[ "$MODE" == "create" ]]; then
  if [[ -z "$FROM_BRANCH" || -z "$RHOAI_VERSION" || -z "$TARGET_BRANCH" ]]; then
    print_error "For create mode, --from, --rhoai-version, and --target are required."
    usage
  fi
elif [[ "$MODE" == "update" ]]; then
  if [[ -z "$TARGET_BRANCH" || -z "$RHOAI_VERSION" ]]; then
    print_error "For update mode, --target and --rhoai-version are required."
    usage
  fi
fi

# Validate branch name formats (rhoai-X.Y where X=single digit, Y=1-2 digits)
validate_rhoai_release_branch_name "$TARGET_BRANCH"
if [[ "$MODE" == "create" ]]; then
  validate_rhoai_release_branch_name "$FROM_BRANCH"
fi

# Validate RHOAI_VERSION format and set version type variables
# Sets: IS_EA_RELEASE, IS_EA_HOTFIX, IS_FIRST_EA, VERSION_TYPE
validate_rhoai_version_format "$RHOAI_VERSION"

# Create mode is only valid for first EA release
if [[ "$MODE" == "create" ]]; then
  if ! is_first_ea_version "$RHOAI_VERSION"; then
    print_error "Create mode is only valid for the first EA release (X.Y.0-ea.1)."
    echo "   Version '$RHOAI_VERSION' is not a first EA release."
    echo "   Use 'update' mode for subsequent releases."
    exit 1
  fi
fi

# Ensure pipelineruns directory exists
validate_path_exists "$PIPELINERUNS_DIR"

# ==============================================================================
# Variable setup
# ==============================================================================

# Parse version into components: BASE_VERSION, MAJOR_VERSION, MINOR_VERSION, MICRO_VERSION
parse_rhoai_version "$RHOAI_VERSION"

# Validate that target branch and version have matching major.minor (for both modes)
validate_branch_version_match "$TARGET_BRANCH"

# Extract EA number and hotfix number if applicable
if [[ "$IS_EA_RELEASE" == "true" ]]; then
  EA_SUFFIX="${RHOAI_VERSION##*-ea.}"
  if [[ "$IS_EA_HOTFIX" == "true" ]]; then
    # EA_SUFFIX is like "1.1" - extract EA number and hotfix number
    EA_NUMBER="${EA_SUFFIX%%.*}"
    HOTFIX_NUMBER="${EA_SUFFIX##*.}"
  else
    # EA_SUFFIX is just the EA number like "1"
    EA_NUMBER="${EA_SUFFIX}"
  fi
fi

# Mode-specific setup
if [[ "$MODE" == "create" ]]; then
  # In create mode, FROM_BRANCH is the source branch (template)
  SOURCE_BRANCH="$FROM_BRANCH"

  # Version strings for sed replacements
  tkn_source_version=${SOURCE_BRANCH/rhoai-/}
  tkn_source_hyphenated_version=${tkn_source_version/./-}
  tkn_target_version=${TARGET_BRANCH/rhoai-/}
  tkn_target_hyphenated_version=${tkn_target_version/./-}

  # File pattern hyphenated version (based on source branch)
  hyphenated_version=${tkn_source_hyphenated_version}
else
  # In update mode, use target branch for file pattern
  hyphenated_version=$(echo "$TARGET_BRANCH" | sed -e 's/^rhoai-//' -e 's/\./-/')
fi

# ==============================================================================
# Print configuration
# ==============================================================================
if [[ "$MODE" == "create" ]]; then
  print_header "Mode: CREATE (New Minor Version)"
  print_info "Creating pipelineruns for ${TARGET_BRANCH} using ${SOURCE_BRANCH} as template"
else
  print_header "Mode: UPDATE (Version Update)"
  print_info "Updating rhoai-version in ${TARGET_BRANCH}"
fi
echo ""
echo "Release Type:      $VERSION_TYPE"
if [[ "$IS_EA_RELEASE" == "true" ]]; then
  echo "EA Number:         $EA_NUMBER"
  if [[ "$IS_EA_HOTFIX" == "true" ]]; then
    echo "Hotfix Number:     $HOTFIX_NUMBER"
  fi
fi
echo "-------------------------------------------"
echo "Pipelineruns Dir:  $PIPELINERUNS_DIR"
if [[ "$MODE" == "create" ]]; then
  echo "Source Branch:     $SOURCE_BRANCH (template)"
fi
echo "Target Branch:     $TARGET_BRANCH"
echo "Target Version:    $RHOAI_VERSION"
echo "Base Version:      $BASE_VERSION"
echo "Major Version:     $MAJOR_VERSION"
echo "Minor Version:     $MINOR_VERSION"
echo "Micro Version:     $MICRO_VERSION"
if [[ "$MODE" == "create" ]]; then
  echo "-------------------------------------------"
  echo "Version Replacements:"
  echo "  ${tkn_source_version} -> ${tkn_target_version}"
  echo "  v${tkn_source_hyphenated_version} -> v${tkn_target_hyphenated_version}"
else
  echo "-------------------------------------------"
  echo "File Pattern:      *v${hyphenated_version}-push*.yaml, *v${hyphenated_version}-scheduled*.yaml"
fi
echo ""

# ==============================================================================
# Process pipelineruns
# ==============================================================================

# Generate a single-line JSON string containing all folder names inside the pipelineruns directory
folders=$(find "${PIPELINERUNS_DIR}" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | jq -R . | jq -s .)
print_info "Folders inside '$PIPELINERUNS_DIR' directory"
echo "$folders" | jq .
echo ""

cd "$PIPELINERUNS_DIR" || exit 1

# Processing Tekton files in each folder one by one
for folder in $(echo "$folders" | jq -r '.[]'); do
  print_header "Processing Tekton Files in Folder: $folder"

  # Ensure .tekton directory exists
  tekton_dir="$folder/.tekton"
  validate_path_exists "${tekton_dir}"

  print_step "Files inside .tekton:"
  find "${tekton_dir}" -type f -exec basename {} \; | sed 's/^/  - /'
  echo ""

  # Only process push and scheduled files (not pull-request)
  # Note: Using find instead of glob because brace expansion doesn't work in variables
  while IFS= read -r file; do

    if [ -f "$file" ]; then
      filename=$(basename "$file")
      echo ""
      print_step "Processing $filename"

      # Extract current rhoai-version from pipelinerun using yq (read-only)
      current_version=$(yq '.spec.params[] | select(.name == "rhoai-version") | .value' "$file")
      echo "Existing rhoai-version: $current_version"

      if [[ -z "$current_version" ]]; then
        print_error "The pipelinerun does not have 'rhoai-version' param set. Exiting!"
        exit 1
      fi

      # Update rhoai-version param using sed (preserves YAML formatting)
      sed -i '/name: rhoai-version/{n;s/value: .*/value: "'"${RHOAI_VERSION}"'"/}' "$file"

      print_success "rhoai-version: ${current_version} -> ${RHOAI_VERSION}"

      # Additional updates for create mode (version references and file renaming)
      if [[ "$MODE" == "create" ]]; then
        # Replace x.y version references (e.g., 3.3 -> 3.4)
        sed -i "s/\b${tkn_source_version}\b/${tkn_target_version}/g" "$file"
        print_success "${tkn_source_version} -> ${tkn_target_version}"

        # Replace x-y hyphenated version references (e.g., v3-3 -> v3-4)
        sed -i "s/\bv${tkn_source_hyphenated_version}\b/v${tkn_target_hyphenated_version}/g" "$file"
        print_success "v${tkn_source_hyphenated_version} -> v${tkn_target_hyphenated_version}"

        # Rename tekton files to match target version
        new_file="${file/v${tkn_source_hyphenated_version}/v${tkn_target_hyphenated_version}}"
        mv "$file" "$new_file"
        print_success "$(basename "$file") -> $(basename "$new_file")"
      fi

    fi

  done < <(find "${tekton_dir}" -type f \( -name "*v${hyphenated_version}-push*.yaml" -o -name "*v${hyphenated_version}-scheduled*.yaml" \) | sort)

  echo ""
done

# ==============================================================================
# Show changes made
# ==============================================================================
print_header "Summary of changes"
set -x
git status
git diff --color=always
set +x

