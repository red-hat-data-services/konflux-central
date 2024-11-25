#!/bin/bash
#Prerequisites
# tracer.sh present in the current dir and configured
# yq
RBC_URL=https://github.com/red-hat-data-services/RHOAI-Build-Config
FBC_QUAY_REPO=quay.io/rhoai/rhoai-fbc-fragment
release_branch=rhoai-2.16
rhoai_version=2.16.0
component_application=rhoai-v2-16
fbc_application_prefix=rhoai-fbc-fragment-ocp-

current_dir=$(pwd)
workspace=$(mktemp -d)
echo "workspace=${workspace}"

# write the code to pull the latest CI/nightly from tracer or take an input fbc-fragment image
# find the RBC_RELEASE_BRANCH_COMMIT from the image using git.commit label
# validate that a snapshot exists for the given commit

RBC_RELEASE_BRANCH_COMMIT=e37a9d0b83b20586a2ddec51fefcb3deda2f595e
echo "starting to find the snapshot built with the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"
#
#
RBC_RELEASE_DIR=${workspace}/RBC_${release_branch}_commit
V417_CATALOG_YAML_PATH=catalog/v4.17/rhods-operator/catalog.yaml
mkdir -p ${RBC_RELEASE_DIR}
cd ${RBC_RELEASE_DIR}
git config --global init.defaultBranch ${release_branch}
git init -q
git remote add origin $RBC_URL
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "${V417_CATALOG_YAML_PATH}" >> .git/info/sparse-checkout
git fetch -q --depth=1 origin ${RBC_RELEASE_BRANCH_COMMIT}
git checkout -q ${RBC_RELEASE_BRANCH_COMMIT}
CATALOG_YAML_PATH=${RBC_RELEASE_DIR}/${V417_CATALOG_YAML_PATH}
cd ${current_dir}

RBC_RELEASE_DIR=${workspace}/RBC_${release_branch}_commit
V417_CATALOG_YAML_PATH=catalog/v4.17/rhods-operator/catalog.yaml
CATALOG_YAML_PATH=${RBC_RELEASE_DIR}/${V417_CATALOG_YAML_PATH}

# find out the expected rhoai images for the given FBCF
expected_rhoai_images_file_path=${workspace}/expected_rhoai_images.json
python release_processor.py --operation extract-rhoai-images-from-catalog --catalog-yaml-path ${CATALOG_YAML_PATH} --rhoai-version ${rhoai_version} --output-file-path ${expected_rhoai_images_file_path}

# find out all the existing snapshots to be explored
component_application_snapshots_path=${workspace}/component_application_snapshots.txt
oc get snapshots -l "pac.test.appstudio.openshift.io/event-type in (push, Push),appstudio.openshift.io/application=${component_application}" --sort-by=.metadata.creationTimestamp --no-headers | awk '{print $1}' | tac > ${component_application_snapshots_path}

while read snapshot_name; do
  snapshot_file_path=${workspace}/${snapshot_name}.json
  oc get snapshot ${snapshot_name} -o=jsonpath='{.spec.components[*].containerImage}' | jq -s -R 'split(" ")' > ${snapshot_file_path}
  python release_processor.py --operation check-snapshot-compatibility --snapshot-file-path ${workspace}/${snapshot_name}.json --expected-rhoai-images-file-path ${expected_rhoai_images_file_path} --snapshot-name ${snapshot_name}
  compatible=$(jq -r '.compatible' ${snapshot_file_path})
  if [[ $compatible == YES ]]; then echo "${snapshot_name} is the correct snapshot to push to stage!"; exit; fi
done <${component_application_snapshots_path}