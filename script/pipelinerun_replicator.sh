#!/bin/bash

# Branch from which Tekton pipelinerun YAMLs will be replicated (e.g., "rhoai-2.20")
SOURCE_BRANCH=""

# Full RHOAI version string in the format vX.Y.Z (e.g., "v2.21.0")
RHOAI_VERSION=""

# Directory containing Tekton pipelinerun YAML files
PIPELINERUNS_DIR=""

# Help message for script usage
usage() {
  echo "Usage  : $0 -s <source_branch> -v <rhoai_version> -d <pipelineruns_dir>"
  echo ""
  echo "Options:"
  echo "  -s <source_branch>   Branch name from which Tekton pipelineruns will be replicated (e.g., 'rhoai-2.20')"
  echo "  -v <rhoai_version>   Full RHOAI version string in the format vX.Y.Z (e.g., 'v2.21.0')"
  echo "  -d <pipelineruns_dir>  Directory containing tekton pipelinerun YAML files"
  echo ""
  echo "Example: $0 -s rhoai-2.20 -v v2.21.0 -d pipelineruns"
  exit 1
}

# Parse arguments
while getopts ":s:t:v:d:" opt; do
  case $opt in
    s)
      SOURCE_BRANCH="$OPTARG"
      ;;
    v)
      RHOAI_VERSION="$OPTARG"
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
if [[ -z "$SOURCE_BRANCH" || -z "$RHOAI_VERSION" || -z "$PIPELINERUNS_DIR" ]]; then
  echo "Error: All arguments are required."
  usage
fi


# Extract major, minor, and micro version from RHOAI_VERSION
MAJOR_VERSION=$(echo "$RHOAI_VERSION" | cut -d'.' -f1 | tr -d 'v')
MINOR_VERSION=$(echo "$RHOAI_VERSION" | cut -d'.' -f2)
MICRO_VERSION=$(echo "$RHOAI_VERSION" | cut -d'.' -f3)

# Determine TARGET_BRANCH from RHOAI_VERSION
TARGET_BRANCH="rhoai-${MAJOR_VERSION}.${MINOR_VERSION}"

# Print the values
echo "-----------------------------------"
echo "Pipelineruns Dir:  $PIPELINERUNS_DIR"
echo "Source Branch:     $SOURCE_BRANCH"
echo "Target Branch:     $TARGET_BRANCH"
echo "RHOAI Version:     $RHOAI_VERSION"
echo "Major Version:     $MAJOR_VERSION"
echo "Minor Version:     $MINOR_VERSION"
echo "Micro Version:     $MICRO_VERSION"
echo "-----------------------------------"


tkn_source_version=${SOURCE_BRANCH/rhoai-/}
tkn_source_hyphenated_version=${tkn_source_version/./-}
tkn_target_version=${TARGET_BRANCH/rhoai-/}
tkn_target_hyphenated_version=${tkn_target_version/./-}
echo "-----------------------------------"
echo "tkn_source_version: ${tkn_source_version}"
echo "tkn_target_version: ${tkn_target_version}"
echo "tkn_source_hyphenated_version: ${tkn_source_hyphenated_version}"
echo "tkn_target_hyphenated_version: ${tkn_target_hyphenated_version}"
echo "-----------------------------------"
echo ""

# Ensure pipelineruns directory exists
if [[ ! -d "$PIPELINERUNS_DIR" ]]; then
  echo "❌ Error: Directory '$PIPELINERUNS_DIR' does not exist. Exiting..."
  exit 1
fi

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
    echo "❌ Error: Directory '${tekton_dir}' does not exist in branch '$SOURCE_BRANCH'. Exiting..."
    exit 1
  fi

  echo "Files inside .tekton:"
  find "${tekton_dir}" -type f -exec basename {} \; | sed 's/^/  - /'
  echo ""
  
  for file in ${tekton_dir}/*${tkn_source_hyphenated_version}-{push,pull-request,scheduled}*.yaml; do
    
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

      if [[ "$konflux_application" != *external* && -z "$label_version" ]]; then
        echo "  ❌ Error: The internal konflux component does not have 'version' LABEL set. Exiting!"
        exit 1
      fi
      
      if [[ "$konflux_application" == *"external"* && -z "$label_version" ]]; then
        echo "  ⚠️  The external konflux component does not have 'version' LABEL set. Skipping!"
      else 
        if [[ "$uses_pipeline_ref" == "true" ]]; then
          ${sed_command} -i '/name: additional-tags/{n;:a;/version=/ {s/version=["]*[^""]*[""]*/version='"$RHOAI_VERSION"'/;b};n;ba}' $file

          # Modelmesh has an additional build argument that needs to be updated as well.
          # https://github.com/red-hat-data-services/modelmesh/blob/36ff14bc/.tekton/odh-modelmesh-v2-22-push.yaml#L41-L43
          if [[ $file == "odh-modelmesh-v${tkn_source_hyphenated_version}-push.yaml" ]]; then
            ${sed_command} -i '/name: build-args/{n;:a;/VERSION=/ {s/VERSION=["]*[^""]*[""]*/VERSION='"$RHOAI_VERSION"'/;b};n;ba}' $file
          fi

        else
          ${sed_command} -i '/name: LABELS/{n;:a;/version=/ {s/version=["]*[^""]*[""]*/version='"$RHOAI_VERSION"'/;b};n;ba}' $file
        fi

        echo "  ✅ version="${label_version}" -> version="${RHOAI_VERSION}" "
      fi


      # Replace x.y references (e.g., 2.20 -> 2.21)
      ${sed_command} -i "s/\b${tkn_source_version}\b/${tkn_target_version}/g" "$file"
      echo "  ✅ ${tkn_source_version} -> ${tkn_target_version}"


      # Replace x-y references (e.g., 2-20 -> 2-21)
      ${sed_command} -i "s/\bv${tkn_source_hyphenated_version}\b/v${tkn_target_hyphenated_version}/g" "$file"
      echo "  ✅ v${tkn_source_hyphenated_version} -> v${tkn_target_hyphenated_version}"

      # rename tekton files
      mv $file ${file/v${tkn_source_hyphenated_version}/v${tkn_target_hyphenated_version}}
      echo "  ✅ $(basename "$file") -> $(basename "${file/v${tkn_source_hyphenated_version}/v${tkn_target_hyphenated_version}}")"

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
