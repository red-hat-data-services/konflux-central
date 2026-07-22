#!/bin/bash


# RHOAI Release Branch
branch="rhoai-2.20"

# konflux application name 
konflux_application="${branch/-/-v}"
konflux_application="${konflux_application//./-}"

# Konflux kubectl config context name
int_konflux_ctx_name="internal"
ext_konflux_ctx_name="internal"

# rhoai version in the form rhoai-vX-YY
rhoai_version="rhoai-v2-20"

# Output filename to store repository list
output_file="repository.txt"



echo "Gathering git repository list for RHOAI version: ${rhoai_version}"
echo "Output will be saved to: ${output_file}"
echo ""

# External context
kubectl config use-context ${ext_konflux_ctx_name}
echo "**************************************************************************************"
echo "Repositories onboarded in external Konflux Application: external-${konflux_application}"
echo "**************************************************************************************"
oc get components -o yaml | yq ".items[] | select(.spec.application == \"external-${konflux_application}\") | .spec.source.git.url" | tee "$output_file"
echo ""

# Internal context
kubectl config use-context ${int_konflux_ctx_name}
echo "**************************************************************************************"
echo "Repositories onboarded in internal Konflux Application: ${konflux_application}"
echo "**************************************************************************************"
oc get components -o yaml | yq ".items[] | select(.spec.application == \"${konflux_application}\") | .spec.source.git.url" | tee -a "$output_file"
echo ""

# Normalize URLs: strip .git suffix if present
echo "> Normalizing URLs (removing .git suffix where present)..."
sed -E 's/\.git$//' "$output_file" > "${output_file}.normalized"

# Sort and deduplicate
echo "> Sorting and removing duplicates..."
sort -u "${output_file}.normalized" -o "$output_file"
rm "${output_file}.normalized"

echo ""
echo "**************************************************************************************"
echo "Final sorted and deduplicated list of git repositories:"
echo "**************************************************************************************"
cat "$output_file"


echo ""
echo "======================== Gathering Pipelineruns For ${branch} ========================"
echo ""

output_folder="pipelineruns"
clone_folder="clone"
rm -rf "$output_folder" "$clone_folder"
mkdir -p "$output_folder" "$clone_folder"

# Arrays to track outcomes
successful=()
missing_tekton=()
failed_clone=()

while IFS= read -r line || [[ -n "$line" ]]; do
  # Trim leading/trailing whitespace
  repo_url="$(echo "$line" | xargs)"

  # Skip empty lines or comments
  if [[ -z "$repo_url" || "$repo_url" =~ ^# ]]; then
    continue
  fi

  repo_name=$(basename "$repo_url")
  
  echo ""
  echo "---------------------------------------------------------------------------"
  echo "Cloning repo: ${repo_url} into ${clone_folder}/${repo_name}"
  echo "---------------------------------------------------------------------------"
  git clone --branch "$branch" --depth 1 "$repo_url" "${clone_folder}/${repo_name}"

  if [ $? -ne 0 ]; then
    echo "❌ Failed to clone $repo_url."
    failed_clone+=("$repo_url")
    continue
  fi

  source="${clone_folder}/${repo_name}/.tekton"
  target="${output_folder}/${repo_name}/"

  if [ -d "${source}" ]; then
    echo "   Copying '${source}' to '${target}'"
    mkdir -p "${output_folder}/${repo_name}"
    cp -r "${source}" "${target}"
    echo "✅ Successfully Copied!"
    successful+=("$repo_url")
  else
    echo "⚠️  No .tekton folder found for $repo_name!"
    missing_tekton+=("$repo_url")
  fi

done < "$output_file"

# Print summary
echo ""
echo ""
echo "==================== Summary ===================="
echo ""

echo "⚠️ Repos missing .tekton folder:"
for repo in "${missing_tekton[@]}"; do
  echo "  - $repo"
done

echo ""
echo "❌ Failed to clone:"
for repo in "${failed_clone[@]}"; do
  echo "  - $repo"
done

echo ""
echo "✅ Repos with .tekton copied:"
for repo in "${successful[@]}"; do
  echo "  - $repo"
done


