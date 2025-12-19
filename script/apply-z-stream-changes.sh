#!/bin/bash

# Branch name for which z-stream changes needs to be applied (e.g., "rhoai-2.20")
BRANCH=""

# Directory containing Tekton pipelinerun YAML files
PIPELINERUNS_DIR=""

# RHOAI-Build-Config directory (optional, for updating patch files)
RBC_DIR=""

# New version value (optional, will be calculated if not provided)
NEW_VERSION=""

# Flag to enable RHOAI-Build-Config updates
UPDATE_RBC=false

# Type of RBC update: "tekton", "patches", or "both" (default: "both")
RBC_UPDATE_TYPE="both"

# Help message for script usage
usage() {
  echo "Usage  : $0 -b <branch> -d <pipelineruns_dir> [OPTIONS]"
  echo ""
  echo "Required Options:"
  echo "  -b <branch>           Branch name for which z-stream changes needs to be applied (e.g., 'rhoai-2.20')"
  echo "  -d <pipelineruns_dir> Directory containing tekton pipelinerun YAML files"
  echo ""
  echo "Optional Options (for RHOAI-Build-Config updates):"
  echo "  -r <rbc_dir>          Directory path to RHOAI-Build-Config repository"
  echo "  -v <new_version>      New version value (e.g., '2.25.2'). If not provided, will be calculated"
  echo "  --update-rbc          Enable RHOAI-Build-Config updates (default: both tekton and patches)"
  echo "  --rbc-type <type>     Type of RBC update: 'tekton', 'patches', or 'both' (default: 'both')"
  echo ""
  echo "Examples:"
  echo "  $0 -b rhoai-2.20 -d pipelineruns"
  echo "  $0 -b rhoai-2.25 -d pipelineruns -r /path/to/RHOAI-Build-Config --update-rbc"
  echo "  $0 -b rhoai-2.25 -d pipelineruns -r /path/to/RHOAI-Build-Config -v 2.25.2 --update-rbc"
  exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -b)
      BRANCH="$2"
      shift 2
      ;;
    -d)
      PIPELINERUNS_DIR="$2"
      shift 2
      ;;
    -r)
      RBC_DIR="$2"
      shift 2
      ;;
    -v)
      NEW_VERSION="$2"
      shift 2
      ;;
    --update-rbc)
      UPDATE_RBC=true
      shift
      ;;
    --rbc-type)
      RBC_UPDATE_TYPE="$2"
      if [[ ! "$RBC_UPDATE_TYPE" =~ ^(tekton|patches|both)$ ]]; then
        echo "Error: --rbc-type must be 'tekton', 'patches', or 'both'" >&2
        exit 1
      fi
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

# used gsed for MacOS
if [[ "$(uname)" == "Darwin" ]]; then
  if ! command -v gsed &>/dev/null; then
      echo "❌ Error: gsed is not installed. Please install it using 'brew install gnu-sed'."
      exit 1
  fi
  sed_command="gsed"
else
  sed_command="sed"
fi

# Validate required arguments
if [[ -z "$BRANCH" ]]; then
  echo "Error: Branch is required."
  usage
fi

# PIPELINERUNS_DIR is only required if not doing RBC-only updates
if [[ -z "$PIPELINERUNS_DIR" && "$UPDATE_RBC" != "true" ]]; then
  echo "Error: Pipelineruns directory is required when not using --update-rbc."
  usage
fi

# Validate RBC update requirements
if [[ "$UPDATE_RBC" == "true" ]]; then
  if [[ -z "$RBC_DIR" ]]; then
    echo "Error: RBC directory (-r) is required when --update-rbc is specified."
    usage
  fi
  if [[ ! -d "$RBC_DIR" ]]; then
    echo "❌ Error: RBC directory '$RBC_DIR' does not exist. Exiting..."
    exit 1
  fi
  # Check if yq is available (required for RBC updates)
  if ! command -v yq &>/dev/null; then
    echo "❌ Error: yq is not installed. Required for RHOAI-Build-Config updates."
    echo "   Install it from: https://github.com/mikefarah/yq"
    exit 1
  fi
fi

# Process pipelineruns only if directory is provided
if [[ -n "$PIPELINERUNS_DIR" ]]; then
  # Ensure pipelineruns directory exists
  if [[ ! -d "$PIPELINERUNS_DIR" ]]; then
    echo "❌ Error: Directory '$PIPELINERUNS_DIR' does not exist. Exiting..."
    exit 1
  fi

  hyphenated_version=$(echo "$BRANCH" | sed -e 's/^rhoai-/v/' -e 's/\./-/')

  # Print the values
  echo "-----------------------------------"
  echo "Pipelineruns Dir   : $PIPELINERUNS_DIR"
  echo "Branch             : $BRANCH"
  echo "Hyphenated Version : $hyphenated_version"
  echo "-----------------------------------"

  # generate a single-line JSON string containing all folder names inside the pipelineruns directory
  folders=$(find ${PIPELINERUNS_DIR} -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | jq -R . | jq -s .)
  echo "Folders inside '$PIPELINERUNS_DIR' directory"
  echo "$folders" | jq .
  echo ""

  cd $PIPELINERUNS_DIR

  # Processing Tekton files in each folder one by one
  for folder in $(echo "$folders" | jq -r '.[]'); do
  echo "============================================================================"
  echo ">> Processing Tekton Files in Folder: $folder"
  echo "============================================================================"
  
  # Ensure .tekton directory exists
  tekton_dir="$folder/.tekton"
  if [[ ! -d "${tekton_dir}" ]]; then
    echo "❌ Error: Directory '${tekton_dir}' does not exist in branch '$BRANCH'. Exiting..."
    exit 1
  fi

  echo "Files inside .tekton:"
  find "${tekton_dir}" -type f -exec basename {} \; | sed 's/^/  - /'
  echo ""
  
  for file in ${tekton_dir}/*${hyphenated_version}-{push,scheduled}*.yaml; do
    
    if [ -f "$file" ]; then
      filename=$(basename $file)
      echo "Processing $(basename $filename)"

      # Updating version label
      konflux_application=$(yq '.metadata.labels."appstudio.openshift.io/application"' $file)

      # check to see if pipelineRefs are being used
      uses_pipeline_ref=$(yq '.spec | has("pipelineRef")' $file)
      
      if [[ "$uses_pipeline_ref" == "true" ]]; then
        echo "$filename appears to use pipelineRefs"
        label_version=$(yq '.spec.params[] | select(.name | test("^additional-labels")) | .value[] | select(test("^version=")) | sub("^version=";"")' $file) 
      else
        label_version=$(yq '.spec.pipelineSpec.tasks[] | select(.name | test("^(build-container|build-images)$")) | .params[] | select(.name == "LABELS") | .value[] | select(test("^version=")) | sub("^version="; "")' $file)
      fi
      echo "Detected label version: $label_version"

      # Extract major, minor, and micro version from RHOAI_VERSION
      MAJOR_VERSION=$(echo "$label_version" | cut -d'.' -f1 | tr -d 'v')
      MINOR_VERSION=$(echo "$label_version" | cut -d'.' -f2)
      MICRO_VERSION=$(echo "$label_version" | cut -d'.' -f3)

      # Determine Z-stream version
      Z_STREAM_VERSION="v${MAJOR_VERSION}.${MINOR_VERSION}.$((MICRO_VERSION + 1))"

      if [[ ( "$konflux_application" == *"external"* || "$konflux_application" == "automation" ) && -z "$label_version" ]]; then
        echo "  ⚠️  The external konflux component does not have 'version' LABEL set. Skipping!"
      elif [[ "$konflux_application" != *external* && -z "$label_version" ]]; then
        echo "  ❌ Error: The internal konflux component does not have 'version' LABEL set. Exiting!"
        exit 1
      else 
        if [[ "$uses_pipeline_ref" == "true" ]]; then
          ${sed_command} -i '/name: additional-tags/{n;:a;/version=/ {s/version=["]*[^""]*[""]*/version='"$Z_STREAM_VERSION"'/;b};n;ba}' $file

          # Modelmesh has an additional build argument that needs to be updated as well.
          # https://github.com/red-hat-data-services/modelmesh/blob/36ff14bc/.tekton/odh-modelmesh-v2-22-push.yaml#L41-L43
          if [[ $filename == odh-modelmesh-v*-push.yaml ]]; then
            echo "  🔔  updating VERSION in build-args!"
            ${sed_command} -i '/name: build-args/{n;:a;/VERSION=/ {s/VERSION=["]*[^""]*[""]*/VERSION='"$Z_STREAM_VERSION"'/;b};n;ba}' $file
          fi

        else
          ${sed_command} -i '/name: LABELS/{n;:a;/version=/ {s/version=["]*[^""]*[""]*/version='"$Z_STREAM_VERSION"'/;b};n;ba}' $file
        fi

        echo "  ✅ version="${label_version}" -> version="${Z_STREAM_VERSION}" "
      fi

    fi
    echo ""
  
  done

    echo ""
  done

  # Show changes made
  set -x
  git status
  git diff --color=always
  set +x
fi

# Function to update RHOAI-Build-Config tekton files
update_rbc_tekton() {
  local rbc_path="$1"
  local branch="$2"
  
  echo ""
  echo "============================================================================"
  echo ">> Updating RHOAI-Build-Config Tekton Files"
  echo "============================================================================"
  echo "RBC Directory: $rbc_path"
  echo "Branch: $branch"
  echo ""
  
  cd "$rbc_path" || exit 1
  
  # Extract x.y from rhoai-x.y format (e.g., rhoai-2.16 -> 216)
  local version_xy=$(echo "$branch" | sed 's/rhoai-//' | tr -d '.')
  echo "Version pattern (xy): $version_xy"
  echo ""
  
  local files_updated=0
  local new_version=""
  
  # Find files matching pattern rhoai-fbc-fragment-rhoai-xy-ocp-*-push.yaml
  while IFS= read -r file; do
    if [[ -f "$file" ]]; then
      echo "Processing file: $file"
      
      # Read current version value
      local current_version=$(yq eval '.spec.params[] | select(.name == "rhoai-version") | .value' "$file")
      
      if [[ -z "$current_version" || "$current_version" == "null" ]]; then
        echo "  ⚠️  Warning: Could not find rhoai-version parameter in $file"
        continue
      fi
      
      echo "  Current version: $current_version"
      
      # Parse version (major.minor.patch)
      IFS='.' read -ra VERSION_PARTS <<< "$current_version"
      local major=${VERSION_PARTS[0]}
      local minor=${VERSION_PARTS[1]}
      local patch=${VERSION_PARTS[2]}
      
      # Increment patch version
      local new_patch=$((patch + 1))
      new_version="${major}.${minor}.${new_patch}"
      
      echo "  New version: $new_version"
      
      # Update the version in the YAML file using yq
      yq eval -i "(.spec.params[] | select(.name == \"rhoai-version\") | .value) = \"${new_version}\"" "$file"
      
      # Verify the update
      local updated_version=$(yq eval '.spec.params[] | select(.name == "rhoai-version") | .value' "$file")
      if [[ "$updated_version" == "$new_version" ]]; then
        echo "  ✅ Successfully updated: $current_version -> $new_version"
        files_updated=$((files_updated + 1))
      else
        echo "  ❌ Error: Failed to update version. Expected: $new_version, Got: $updated_version"
        exit 1
      fi
      echo ""
    fi
  done < <(find .tekton -name "rhoai-fbc-fragment-rhoai-${version_xy}-ocp-*-push.yaml" -type f 2>/dev/null || true)
  
  if [[ $files_updated -eq 0 ]]; then
    echo "⚠️  No matching files found or updated"
    echo "   Pattern: rhoai-fbc-fragment-rhoai-${version_xy}-ocp-*-push.yaml"
  else
    echo "✅ Updated $files_updated file(s)"
    echo ">>> New version calculated: $new_version"
    # Write version to file for workflow to read
    echo "$new_version" > "$rbc_path/.rbc_new_version.txt"
  fi
  
  echo ""
  echo ">>> Summary of changes:"
  git diff --stat
  echo ""
  
  cd - > /dev/null || exit 1
  
  # Return new version via stdout (for script chaining)
  if [[ -n "$new_version" ]]; then
    echo "$new_version"
  fi
}

# Function to update RHOAI-Build-Config patch files
update_rbc_patches() {
  local rbc_path="$1"
  local new_version="$2"
  
  echo ""
  echo "============================================================================"
  echo ">> Updating RHOAI-Build-Config Patch Files"
  echo "============================================================================"
  echo "RBC Directory: $rbc_path"
  echo "New Version: $new_version"
  echo ""
  
  cd "$rbc_path" || exit 1
  
  # Extract old version from bundle-patch.yaml or catalog-patch.yaml
  local old_version=""
  if [[ -f "bundle/bundle-patch.yaml" ]]; then
    old_version=$(yq eval '.patch.version' bundle/bundle-patch.yaml 2>/dev/null || echo "")
  fi
  
  if [[ -z "$old_version" || "$old_version" == "null" ]]; then
    if [[ -f "catalog/catalog-patch.yaml" ]]; then
      old_version=$(yq eval '.olm.channels[0].entries[0].name' catalog/catalog-patch.yaml 2>/dev/null | sed 's/rhods-operator\.//' || echo "")
    fi
  fi
  
  if [[ -z "$old_version" || "$old_version" == "null" ]]; then
    echo "⚠️  Warning: Could not determine old version, using new version"
    old_version="$new_version"
  fi
  
  echo "Old Version: $old_version"
  echo "New Version: $new_version"
  echo ""
  
  # 1. Update config files productVersion
  echo ">>> Updating config files productVersion..."
  if [[ -f "config/modelmesh-pig-build-config.yaml" ]]; then
    echo "  Updating config/modelmesh-pig-build-config.yaml"
    ${sed_command} -i "1s|#!productVersion=.*|#!productVersion=${new_version}|" config/modelmesh-pig-build-config.yaml
    echo "  ✅ Updated productVersion to ${new_version}"
  else
    echo "  ⚠️  config/modelmesh-pig-build-config.yaml not found"
  fi
  
  if [[ -f "config/trustyai-pig-build-config.yaml" ]]; then
    echo "  Updating config/trustyai-pig-build-config.yaml"
    ${sed_command} -i "1s|#!productVersion=.*|#!productVersion=${new_version}|" config/trustyai-pig-build-config.yaml
    echo "  ✅ Updated productVersion to ${new_version}"
  else
    echo "  ⚠️  config/trustyai-pig-build-config.yaml not found"
  fi
  
  # 2. Update bundle-patch.yaml version
  echo ""
  echo ">>> Updating bundle/bundle-patch.yaml..."
  if [[ -f "bundle/bundle-patch.yaml" ]]; then
    yq eval -i ".patch.version = \"${new_version}\"" bundle/bundle-patch.yaml
    echo "  ✅ Updated bundle patch version to ${new_version}"
  else
    echo "  ⚠️  bundle/bundle-patch.yaml not found"
  fi
  
  # 3. Update catalog-patch.yaml
  echo ""
  echo ">>> Updating catalog/catalog-patch.yaml..."
  if [[ -f "catalog/catalog-patch.yaml" ]]; then
    local skip_range=">=2.24.0 <${new_version}"
    local channel_count=$(yq eval '.olm.channels | length' catalog/catalog-patch.yaml)
    
    for i in $(seq 0 $((channel_count - 1))); do
      echo "  Processing channel $i"
      local entry_count=$(yq eval ".olm.channels[$i].entries | length" catalog/catalog-patch.yaml)
      
      for j in $(seq 0 $((entry_count - 1))); do
        # Get current name
        local current_name=$(yq eval ".olm.channels[$i].entries[$j].name" catalog/catalog-patch.yaml)
        
        # Update name: rhods-operator.x.y.z -> rhods-operator.{NEW_VERSION}
        local new_name=$(echo "$current_name" | ${sed_command} "s/rhods-operator\.[0-9]\+\.[0-9]\+\.[0-9]\+/rhods-operator.${new_version}/")
        yq eval -i ".olm.channels[$i].entries[$j].name = \"${new_name}\"" catalog/catalog-patch.yaml
        
        # Update replaces: copy old name value
        yq eval -i ".olm.channels[$i].entries[$j].replaces = \"${current_name}\"" catalog/catalog-patch.yaml
        
        # Update skipRange
        yq eval -i ".olm.channels[$i].entries[$j].skipRange = \"${skip_range}\"" catalog/catalog-patch.yaml
        
        echo "    Entry $j: ${current_name} -> ${new_name}"
        echo "      replaces: ${current_name}"
        echo "      skipRange: ${skip_range}"
      done
    done
    echo "  ✅ Updated catalog-patch.yaml"
  else
    echo "  ⚠️  catalog/catalog-patch.yaml not found"
  fi
  
  echo ""
  echo ">>> Summary of changes:"
  git diff --stat
  echo ""
  
  cd - > /dev/null || exit 1
}

# Update RHOAI-Build-Config files if requested
if [[ "$UPDATE_RBC" == "true" ]]; then
  local calculated_version=""
  
  # Update tekton files first if needed (to get the new version)
  if [[ "$RBC_UPDATE_TYPE" == "tekton" || "$RBC_UPDATE_TYPE" == "both" ]]; then
    calculated_version=$(update_rbc_tekton "$RBC_DIR" "$BRANCH")
    if [[ -n "$calculated_version" ]]; then
      NEW_VERSION="$calculated_version"
      echo ">>> Using version from tekton files: $NEW_VERSION"
    fi
  fi
  
  # Calculate new version if not provided and not calculated from tekton
  if [[ -z "$NEW_VERSION" ]]; then
    echo "⚠️  Warning: New version not provided and could not be calculated from tekton files"
    echo "   Attempting to calculate from branch..."
    MAJOR_MINOR=$(echo "$BRANCH" | sed 's/rhoai-//')
    # Default to .1 if we can't determine
    NEW_VERSION="${MAJOR_MINOR}.1"
    echo "   Using calculated value: ${NEW_VERSION}"
    echo "   Consider providing -v option for accurate version"
  fi
  
  # Update patch files if needed
  if [[ "$RBC_UPDATE_TYPE" == "patches" || "$RBC_UPDATE_TYPE" == "both" ]]; then
    if [[ -z "$NEW_VERSION" ]]; then
      echo "❌ Error: New version is required for patch updates but could not be determined"
      exit 1
    fi
    update_rbc_patches "$RBC_DIR" "$NEW_VERSION"
  fi
  
  echo ""
  echo "============================================================================"
  echo ">> RHOAI-Build-Config Updates Complete"
  echo "============================================================================"
  echo "Final version used: $NEW_VERSION"
fi
