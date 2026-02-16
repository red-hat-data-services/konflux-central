#!/bin/bash

# Branch name for which z-stream changes needs to be applied (e.g., "rhoai-2.20")
BRANCH=""

# Directory containing Tekton pipelinerun YAML files
PIPELINERUNS_DIR=""

# Help message for script usage
usage() {
  echo "Usage  : $0 -b <branch> -d <pipelineruns_dir>"
  echo ""
  echo "Options:"
  echo "  -b <branch>   Branch name for which z-stream changes needs to be applied (e.g., 'rhoai-2.20')"
  echo "  -d <pipelineruns_dir>  Directory containing tekton pipelinerun YAML files"
  echo ""
  echo "Example: $0 -b rhoai-2.20 -d pipelineruns"
  exit 1
}

# Parse arguments
while getopts ":b:d:" opt; do
  case $opt in
    b)
      BRANCH="$OPTARG"
      ;;
    d)
      PIPELINERUNS_DIR="$OPTARG"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
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
if [[ -z "$BRANCH" || -z "$PIPELINERUNS_DIR" ]]; then
  echo "Error: All arguments are required."
  usage
fi

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
        label_version=$(yq '.spec.params[] | select(.name == "rhoai-version") | .value' $file)
      else
        label_version=$(yq '.spec.pipelineSpec.tasks[] | select(.name | test("^(build-container|build-images)$")) | .params[] | select(.name == "LABELS") | .value[] | select(test("^version=")) | sub("^version=v?"; "")' $file)
      fi
      echo "Detected label version: $label_version"

      # Extract major, minor, and micro version from RHOAI_VERSION
      MAJOR_VERSION=$(echo "$label_version" | cut -d'.' -f1)
      MINOR_VERSION=$(echo "$label_version" | cut -d'.' -f2)
      MICRO_VERSION=$(echo "$label_version" | cut -d'.' -f3)

      # Determine Z-stream version
      Z_STREAM_VERSION="${MAJOR_VERSION}.${MINOR_VERSION}.$((MICRO_VERSION + 1))"

      if [[ ( "$konflux_application" == *"external"* || "$konflux_application" == "automation" ) && -z "$label_version" ]]; then
        echo "  ⚠️  The external konflux component does not have 'version' LABEL set. Skipping!"
      elif [[ "$konflux_application" != *external* && -z "$label_version" ]]; then
        echo "  ❌ Error: The internal konflux component does not have 'version' LABEL set. Exiting!"
        exit 1
      else 
        if [[ "$uses_pipeline_ref" == "true" ]]; then
          #yq -i "(.spec.params[] | select(.name == \"rhoai-version\") | .value) = \"${Z_STREAM_VERSION}\"" $file
          ${sed_command} -i '/name: rhoai-version/{n;s/value: .*/value: "'"${Z_STREAM_VERSION}"'"/}' $file
          # Modelmesh has an additional build argument that needs to be updated as well.
          # https://github.com/red-hat-data-services/modelmesh/blob/36ff14bc/.tekton/odh-modelmesh-v2-22-push.yaml#L41-L43
          if [[ $filename == odh-modelmesh-v*-push.yaml ]]; then
            echo "  🔔  updating VERSION in build-args!"
            ${sed_command} -i '/name: build-args/{n;:a;/VERSION=/ {s/VERSION=["]*[^""]*[""]*/VERSION=v'"$Z_STREAM_VERSION"'/;b};n;ba}' $file
          fi

        else
          ${sed_command} -i '/name: LABELS/{n;:a;/version=/ {s/version=["]*[^""]*[""]*/version=v'"$Z_STREAM_VERSION"'/;b};n;ba}' $file
        fi

        echo "  ✅ version=${label_version} -> version=${Z_STREAM_VERSION}"
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
