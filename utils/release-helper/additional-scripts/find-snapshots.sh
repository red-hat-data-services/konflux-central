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

RBC_RELEASE_DIR=${workspace}/RBC_${release_branch}_main
mkdir -p ${RBC_RELEASE_DIR}
cd ${RBC_RELEASE_DIR}
git config --global init.defaultBranch ${release_branch}
git init -q
git remote add origin $RBC_URL
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "config/build-config.yaml" >> .git/info/sparse-checkout
git fetch -q --depth=1 origin ${release_branch}
git checkout -q ${release_branch}
#git clone -q ${RBC_URL} --branch ${release_branch} ${RBC_RELEASE_DIR}
BUILD_CONFIG_PATH=${RBC_RELEASE_DIR}/config/build-config.yaml
cd ${pwd}



readarray ocp_versions < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
first_ocp_version=$(echo ${ocp_versions[0]} | tr -d '\n')
fbc_application_tag=ocp-${first_ocp_version/v4/4}-${release_branch}
first_image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
META=$(skopeo inspect "${first_image_uri}")
RBC_RELEASE_BRANCH_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')
echo "RBC_MAIN_COMMIT=${RBC_RELEASE_BRANCH_COMMIT}"


  while IFS= read -r ocp_version;
  do
    fbc_application_suffix=${ocp_version/v4./4}
    fbc_application_name=${fbc_application_prefix}${fbc_application_suffix}
    fbc_application_tag=ocp-${ocp_version/v4/4}-${release_branch}
    echo "fbc_application_name=${fbc_application_name}"
    echo "fbc_application_tag=${fbc_application_tag}"

    image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
    META=$(skopeo inspect "${image_uri}")
    FULL_IMAGE_URI_WITH_DIGEST="${FBC_QUAY_REPO}@${DIGEST}"
    echo "FBCF-${ocp_version} - ${FULL_IMAGE_URI_WITH_DIGEST}"
    RBC_CURRENT_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')
    DIGEST=$(echo $META | jq -r .Digest)
    if [[ ${RBC_CURRENT_COMMIT} != ${RBC_RELEASE_BRANCH_COMMIT} ]]
    then
      echo "Stage FBC images are out of sync, it might be because push-to-stage is in progress, please try after sometime or contact the DevOps team.."
      exit 1
    fi
  done < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
echo "all FBC images tag are matching!"

#
echo "starting to find the snapshot built with the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"
RBC_RELEASE_BRANCH_COMMIT=
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
expected_rhoai_images_file_path=${workspace}/expected_rhoai_images.json
#python release_processor.py --operation extract-rhoai-images-from-catalog --catalog-yaml-path ${CATALOG_YAML_PATH} --rhoai-version ${rhoai_version} --output-file-path ${expected_rhoai_images_file_path}

component_application_snapshots_path=${workspace}/component_application_snapshots.txt
oc get snapshots -l "pac.test.appstudio.openshift.io/event-type in (push, Push),appstudio.openshift.io/application=${component_application}" --sort-by=.metadata.creationTimestamp --no-headers | awk '{print $1}' | tac > ${component_application_snapshots_path}


oc get snapshot ${snapshot_name} -o=jsonpath='{range .spec.components[*]}{.containerImage}{"\n"}{end}' | jq -s -R 'split("\n")' > ${snapshot_file_path}

while read snapshot_name; do
  snapshot_file_path=${workspace}/${snapshot_name}.json
  oc get snapshot ${snapshot_name} -o=jsonpath='{.spec.components[*].containerImage}' | jq -s -R 'split(" ")' > ${snapshot_file_path}
  python release_processor.py --operation check-snapshot-compatibility --snapshot-file-path ${workspace}/${snapshot_name}.json --expected-rhoai-images-file-path ${expected_rhoai_images_file_path} --snapshot-name ${snapshot_name}
  compatible=$(jq -r '.compatible' ${snapshot_file_path})
  if [[ $compatible == YES ]]; then echo "${snapshot_name} is the correct snapshot to push to stage!"; exit; fi
done <${component_application_snapshots_path}