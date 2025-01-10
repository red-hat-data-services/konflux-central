#!/bin/bash
#Prerequisites
# yq
# ~/.ssh/.quay_devops_application_token

release_branch=rhoai-2.16
rhoai_version=2.16.0
hyphenized_rhoai_version=v2-16

image_uri=LATEST_NIGHTLY
#image_uri="quay.io/rhoai/rhoai-fbc-fragment@sha256:9c39ccac201a8d2febe05916066faee2828db2e0388f608620d8665148774863"

FBC_QUAY_REPO=quay.io/rhoai/rhoai-fbc-fragment
RBC_URL=https://github.com/red-hat-data-services/RHOAI-Build-Config


if [[ $image_uri == LATEST_NIGHTLY ]]; then image_uri=docker://${FBC_QUAY_REPO}:${release_branch}-nightly; fi
if [[ "$image_uri" != docker* ]]; then image_uri="docker://${image_uri}"; fi

META=$(skopeo inspect "${image_uri}")
DIGEST=$(echo $META | jq -r .Digest)
image_uri=${FBC_QUAY_REPO}@${DIGEST}
RBC_RELEASE_BRANCH_COMMIT=$(echo $META | jq -r '.Labels | ."git.commit"')
SHORT_COMMIT=${RBC_RELEASE_BRANCH_COMMIT::8}

echo "RBC_RELEASE_BRANCH_COMMIT = ${RBC_RELEASE_BRANCH_COMMIT}"
echo "Pushing the components to stage for nightly - ${image_uri}"
echo "starting to create the artifacts corresponding to the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"
#


component_application=rhoai-${hyphenized_rhoai_version}

fbc_application_prefix=rhoai-fbc-fragment-ocp-

RHOAI_QUAY_API_TOKEN=$(cat ~/.ssh/.quay_devops_application_token)

current_dir=$(pwd)
#workspace=/tmp/tmp.68Vts9xj27
#epoch=1732618870

workspace=$(mktemp -d)
echo "workspace=${workspace}"

epoch=$(date +%s)
release_artifacts_dir=stage-release-${SHORT_COMMIT}
release_components_dir=${release_artifacts_dir}/release-components
snapshot_components_dir=${release_artifacts_dir}/snapshot-components

mkdir -p ${release_components_dir}
mkdir -p ${snapshot_components_dir}

template_dir=templates/stage

#RBC_RELEASE_BRANCH_COMMIT=7da42450e089babe0dc31f182e78152c349f201a


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

RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH=${workspace}/konflux_components.txt

kubectl get components -o=jsonpath="{range .items[?(@.spec.application=='${component_application}')]}{@.metadata.name}{'\t'}{@.spec.containerImage}{'\n'}{end}" > ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH}


RHOAI_QUAY_API_TOKEN=${RHOAI_QUAY_API_TOKEN} python release_processor.py --operation generate-release-artifacts --catalog-yaml-path ${CATALOG_YAML_PATH} --konflux-components-details-file-path ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH} --rhoai-version ${rhoai_version} --rhoai-application ${component_application} --epoch ${epoch} --output-dir ${release_artifacts_dir} --template-dir ${template_dir} --rbc-release-commit ${RBC_RELEASE_BRANCH_COMMIT}

cd ${release_artifacts_dir}

#Create components snapshot
oc apply -f snapshot-components

sleep 5
#Start components release
oc apply -f release-components

sleep 10

echo -en "1. Please watch following pipelinerun until green \n2. Run the FBC push to stage for image ${image_uri}\n"
oc get pipelinerun -n rhtap-releng-tenant -l appstudio.openshift.io/snapshot=${component_application}-${epoch}