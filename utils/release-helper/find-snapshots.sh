#!/bin/bash
#Prerequisites
# tracer.sh present in the current dir and configured
# yq
RBC_URL=https://github.com/rhoai-rhtap/RHOAI-Build-Config
FBC_QUAY_REPO=quay.io/rhoai/rhoai-fbc-fragment
rhoai_version=rhoai-2.13

current_dir=$(pwd)
workspace=$(mktemp -d)
RBC_RELEASE_DIR=${workspace}/RBC_${rhoai_version}
mkdir -p ${RBC_RELEASE_DIR}
cd ${RBC_RELEASE_DIR}
git config --global init.defaultBranch ${rhoai_version}
git init -q
git remote add origin $RBC_URL
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "config/build-config.yaml" >> .git/info/sparse-checkout
git fetch -q --depth=1 origin ${rhoai_version}
git checkout -q ${rhoai_version}
#git clone -q ${RBC_URL} --branch ${rhoai_version} ${RBC_RELEASE_DIR}
BUILD_CONFIG_PATH=${RBC_RELEASE_DIR}/config/build-config.yaml
cd ${pwd}



#oc get snapshots -l "pac.test.appstudio.openshift.io/event-type in (push, Push),appstudio.openshift.io/application=${application}" --sort-by=.metadata.creationTimestamp

component_application=rhoai-v2-13
readarray ocp_versions < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
first_ocp_version=$(echo ${ocp_versions[0]} | tr -d '\n')
fbc_application_tag=ocp-${first_ocp_version/v4/4}-${rhoai_version}
first_image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
META=$(skopeo inspect "${first_image_uri}")
RBC_RELEASE_BRANCH_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')
echo "RBC_MAIN_COMMIT=${RBC_RELEASE_BRANCH_COMMIT}"

fbc_application_prefix=rhoai-fbc-poc-
  while IFS= read -r ocp_version;
  do
    fbc_application_suffix=${ocp_version/v4./4}
    fbc_application_name=${fbc_application_prefix}${fbc_application_suffix}
    fbc_application_tag=ocp-${ocp_version/v4/4}-${rhoai_version}
    echo "fbc_application_name=${fbc_application_name}"
    echo "fbc_application_tag=${fbc_application_tag}"

    image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
    META=$(skopeo inspect "${image_uri}")
    RBC_CURRENT_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')

    if [[ ${RBC_CURRENT_COMMIT} != ${RBC_RELEASE_BRANCH_COMMIT} ]]
    then
      echo "Stage FBC images are out of sync, it might be because push-to-stage is in progress, please try after sometime or contact the DevOps team.."
      exit 1
    fi
  done < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
echo "all FBC images tag are matching!"
rm -rf ${workspace}

echo "starting to find the snapshot built with the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"

current_dir=$(pwd)
workspace=$(mktemp -d)
RBC_RELEASE_DIR=${workspace}/RBC_${rhoai_version}
V417_CATALOG_YAML_PATH=catalog/v4.17/rhods-operator/catalog.yaml
mkdir -p ${RBC_RELEASE_DIR}
cd ${RBC_RELEASE_DIR}
git config --global init.defaultBranch ${rhoai_version}
git init -q
git remote add origin $RBC_URL
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "${V417_CATALOG_YAML_PATH}" >> .git/info/sparse-checkout
git fetch -q --depth=1 origin ${RBC_RELEASE_BRANCH_COMMIT}
git checkout -q ${RBC_RELEASE_BRANCH_COMMIT}
CATALOG_YAML_PATH=${RBC_RELEASE_DIR}/${V417_CATALOG_YAML_PATH}
cd ${pwd}