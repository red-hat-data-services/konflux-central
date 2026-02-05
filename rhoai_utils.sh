#!/bin/bash

# ==============================================================================
# RHOAI Utility Functions
# ==============================================================================
# Generic utility functions for RHOAI scripts. Can be sourced by any script
# that needs these common utilities.
# ==============================================================================

# ==============================================================================
# Color Codes
# ==============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ==============================================================================
# Logging Functions
# ==============================================================================

# Print a header with blue color
# Usage: print_header "Setting up environment"
print_header() {
  echo -e "${BLUE}============================================${NC}"
  echo -e "${BLUE}$1${NC}"
  echo -e "${BLUE}============================================${NC}"
}

# Print a step indicator with magenta color
# Usage: print_step "Processing files..."
print_step() {
  echo -e "${MAGENTA}→ $1${NC}"
}

# Print a success message with green color and checkmark
# Usage: print_success "Files processed successfully"
print_success() {
  echo -e "✅ ${GREEN} $1${NC}"
}

# Print an error message with red color and cross mark
# Usage: print_error "Failed to process files"
print_error() {
  echo -e "❌ ERROR: ${RED} $1${NC}"
}

# Print an info message with cyan color
# Usage: print_info "Using default configuration"
print_info() {
  echo -e "${CYAN}ℹ $1${NC}"
}

# Print a warning message with yellow color
# Usage: print_warning "Configuration file not found, using defaults"
print_warning() {
  echo -e "⚠️ WARNING: ${YELLOW} $1${NC}"
}

# ==============================================================================
# Regex Patterns
# ==============================================================================

# Branch format: rhoai-X.Y where X=0-9, Y=0-99 (e.g., rhoai-3.4, rhoai-3.14)
readonly RHOAI_BRANCH_REGEX="^rhoai-[0-9]\.[0-9]{1,2}$"

# GA version: X.Y.Z where X=0-9, Y=0-99, Z=0-99 (e.g., 3.4.0, 3.14.1)
readonly RHOAI_GA_VERSION_REGEX="^[0-9]\.[0-9]{1,2}\.[0-9]{1,2}$"

# EA version: X.Y.Z-ea.N (e.g., 3.4.0-ea.1, 3.14.0-ea.2)
readonly RHOAI_EA_VERSION_REGEX="^[0-9]\.[0-9]{1,2}\.[0-9]{1,2}-ea\.[0-9]+$"

# EA Hotfix version: X.Y.Z-ea.N.H (e.g., 3.4.0-ea.1.1, 3.14.0-ea.2.3)
readonly RHOAI_EA_HOTFIX_VERSION_REGEX="^[0-9]\.[0-9]{1,2}\.[0-9]{1,2}-ea\.[0-9]+\.[0-9]+$"

# ==============================================================================
# Validate that a path exists
# Args: $1 = path
# Returns: 0 if exists, exits with error if not
# ==============================================================================
validate_path_exists() {
  local path="$1"

  if [[ ! -e "$path" ]]; then
    print_error "Path '$path' does not exist."
    exit 1
  fi
}

# ==============================================================================
# Validate RHOAI release branch name format
# Args: $1 = branch name
# Returns: 0 if valid, exits with error if invalid
# ==============================================================================
validate_rhoai_release_branch_name() {
  local branch="$1"

  if [[ ! "$branch" =~ $RHOAI_BRANCH_REGEX ]]; then
    print_error "Invalid release branch name '${branch}'."
    echo "   Branch name must be in format 'rhoai-X.Y' where:"
    echo "     - X is a single digit (0-9)"
    echo "     - Y is 1-2 digits (0-99)"
    echo "   Examples: 'rhoai-3.4', 'rhoai-3.14', 'rhoai-4.0'"
    exit 1
  fi
}

# ==============================================================================
# Validate RHOAI version format
# Args: $1 = version string
# Returns: Sets global variables IS_EA_RELEASE, IS_EA_HOTFIX, IS_FIRST_EA, VERSION_TYPE
#          Exits with error if invalid
# ==============================================================================
validate_rhoai_version_format() {
  local version="$1"

  # Check for 'v' prefix
  if [[ "$version" =~ ^v ]]; then
    print_error "RHOAI version should not have 'v' prefix."
    echo "   Supported formats: X.Y.Z (GA), X.Y.Z-ea.N (EA), or X.Y.Z-ea.N.H (EA Hotfix)"
    echo "   Where X is single digit (0-9), Y and Z are 1-2 digits (0-99)"
    exit 1
  fi

  # Check if version matches GA, EA, or EA Hotfix format
  if [[ "$version" =~ $RHOAI_GA_VERSION_REGEX ]]; then
    IS_EA_RELEASE="false"
    IS_EA_HOTFIX="false"
    IS_FIRST_EA="false"
    VERSION_TYPE="GA"
  elif [[ "$version" =~ $RHOAI_EA_VERSION_REGEX ]]; then
    IS_EA_RELEASE="true"
    IS_EA_HOTFIX="false"
    VERSION_TYPE="EA"
    # Check if it's the first EA (ea.1)
    local ea_num="${version##*-ea.}"
    if [[ "$ea_num" == "1" ]]; then
      IS_FIRST_EA="true"
    else
      IS_FIRST_EA="false"
    fi
  elif [[ "$version" =~ $RHOAI_EA_HOTFIX_VERSION_REGEX ]]; then
    IS_EA_RELEASE="true"
    IS_EA_HOTFIX="true"
    IS_FIRST_EA="false"
    VERSION_TYPE="EA Hotfix"
  else
    print_error "Invalid RHOAI version format '$version'"
    echo "   Supported formats (X=single digit 0-9, Y/Z=1-2 digits 0-99):"
    echo "     - GA release:   X.Y.Z          (e.g., 3.4.0, 3.14.1)"
    echo "     - EA release:   X.Y.Z-ea.N     (e.g., 3.4.0-ea.1)"
    echo "     - EA hotfix:    X.Y.Z-ea.N.H   (e.g., 3.4.0-ea.1.1)"
    exit 1
  fi
}

# ==============================================================================
# Parse RHOAI version string and set base, major, minor and micro versions as global variables
# Args: $1 = version string (e.g., "3.4.0", "3.4.0-ea.1", "3.4.0-ea.1.1")
# Sets global variables:
#   - BASE_VERSION: X.Y.Z without EA suffix
#   - MAJOR_VERSION: X
#   - MINOR_VERSION: Y
#   - MICRO_VERSION: Z
# ==============================================================================
parse_rhoai_version() {
  local version="$1"
  # Extract base version (X.Y.Z) without EA suffix
  BASE_VERSION="${version%%-ea.*}"
  # Extract major, minor, and micro version from base version
  MAJOR_VERSION=$(echo "$BASE_VERSION" | cut -d'.' -f1)
  MINOR_VERSION=$(echo "$BASE_VERSION" | cut -d'.' -f2)
  MICRO_VERSION=$(echo "$BASE_VERSION" | cut -d'.' -f3)
}

# ==============================================================================
# Validate that branch and version have matching major.minor
# Args: $1 = branch name (e.g., "rhoai-3.4")
# Requires: MAJOR_VERSION and MINOR_VERSION global variables to be set
#           (call parse_rhoai_version() first)
# Returns: 0 if matching, exits with error if mismatch
# ==============================================================================
validate_branch_version_match() {
  local branch="$1"

  # Extract major.minor from branch name (e.g., "rhoai-3.4" -> "3.4")
  local branch_major_minor="${branch#rhoai-}"
  local version_major_minor="${MAJOR_VERSION}.${MINOR_VERSION}"

  if [[ "$branch_major_minor" != "$version_major_minor" ]]; then
    print_error "Branch and rhoai-version must have matching major.minor."
    echo "   Branch: $branch"
    echo "   rhoai-version: ${BASE_VERSION}"
    exit 1
  fi
}

# ==============================================================================
# Check if version is first EA release (X.Y.0-ea.1)
# Args: $1 = version string
# Returns: 0 (true) if first EA, 1 (false) otherwise
# ==============================================================================
is_first_ea_version() {
  local version="$1"
  if [[ "$version" =~ ^[0-9]\.[0-9]{1,2}\.0-ea\.1$ ]]; then
    return 0
  else
    return 1
  fi
}
