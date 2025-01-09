#!/bin/bash
#Prerequisites
# tracer.sh present in the current dir and configured
# yq
RBC_URL=https://github.com/red-hat-data-services/RHOAI-Build-Config
FBC_QUAY_REPO=quay.io/rhoai/rhoai-fbc-fragment
release_branch=rhoai-2.16
rhoai_version=2.16.0
hyphenized_rhoai_version=v2-16
component_application=rhoai-${hyphenized_rhoai_version}

fbc_application_prefix=rhoai-fbc-fragment-ocp-

RHOAI_QUAY_API_TOKEN=$(cat ~/.ssh/.quay_devops_application_token)

current_dir=$(pwd)
#workspace=/tmp/tmp.68Vts9xj27
#epoch=1732618870

workspace=$(mktemp -d)
echo "workspace=${workspace}"

epoch=$(date +%s)
release_artifacts_dir=stage-release-${epoch}
release_components_dir=${release_artifacts_dir}/release-components
release_fbc_dir=${release_artifacts_dir}/release-fbc
release_fbc_addon_dir=${release_artifacts_dir}/release-fbc-addon
snapshot_components_dir=${release_artifacts_dir}/snapshot-components
snapshot_fbc_dir=${release_artifacts_dir}/snapshot-fbc

mkdir -p ${release_components_dir}
mkdir -p ${release_fbc_dir}
mkdir -p ${release_fbc_addon_dir}
mkdir -p ${snapshot_components_dir}
mkdir -p ${snapshot_fbc_dir}

template_dir=templates/stage

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
cd ${current_dir}



readarray ocp_versions < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
first_ocp_version=$(echo ${ocp_versions[0]} | tr -d '\n')
fbc_application_tag=ocp-${first_ocp_version/v4/4}-${release_branch}
first_image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
META=$(skopeo inspect "${first_image_uri}")
RBC_RELEASE_BRANCH_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')
echo "RBC_RELEASE_BRANCH_COMMIT=${RBC_RELEASE_BRANCH_COMMIT}"


  while IFS= read -r ocp_version;
  do
    fbc_application_suffix=${ocp_version/v4./4}
    fbc_application_name=${fbc_application_prefix}${fbc_application_suffix}
    fbc_application_tag=ocp-${ocp_version/v4/4}-${release_branch}
    echo "fbc_application_name=${fbc_application_name}"
    echo "fbc_application_tag=${fbc_application_tag}"

    image_uri=docker://${FBC_QUAY_REPO}:${fbc_application_tag}
    META=$(skopeo inspect "${image_uri}")
    DIGEST=$(echo $META | jq -r .Digest)
    FULL_IMAGE_URI_WITH_DIGEST="${FBC_QUAY_REPO}@${DIGEST}"
    echo "FBCF-${ocp_version} - ${FULL_IMAGE_URI_WITH_DIGEST}"
    RBC_CURRENT_COMMIT=$(echo $META | jq -r '.Labels | ."rbc-release-branch.commit"')
    GIT_URL=$(echo $META | jq -r '.Labels | ."git.url"')
    GIT_COMMIT=$(echo $META | jq -r '.Labels | ."git.commit"')

    if [[ ${RBC_CURRENT_COMMIT} != ${RBC_RELEASE_BRANCH_COMMIT} ]]
    then
      echo "Stage FBC images are out of sync, it might be because push-to-stage is in progress, please try after sometime or contact the DevOps team.."
      exit 1
    fi

    # generate FBC Release CR
    fbc_release_yaml_path=${release_fbc_dir}/release-fbc-stage-ocp-${fbc_application_suffix}-${component_application}-${epoch}.yaml
    cp ${template_dir}/release-fbc-stage.yaml ${fbc_release_yaml_path}
    sed -i "s/{{fbc_application}}/${fbc_application_name}/g" ${fbc_release_yaml_path}
    sed -i "s/{{epoch}}/${epoch}/g" ${fbc_release_yaml_path}
    sed -i "s/{{ocp-version}}/ocp-${fbc_application_suffix}/g" ${fbc_release_yaml_path}
    sed -i "s/{{hyphenized-rhoai-version}}/${hyphenized_rhoai_version}/g" ${fbc_release_yaml_path}
    sed -i "s/{{rbc_release_commit}}/${RBC_RELEASE_BRANCH_COMMIT}/g" ${fbc_release_yaml_path}

    if [[ "${fbc_application_suffix}" == "416" ]]
    then
      fbc_addon_release_yaml_path=${release_fbc_addon_dir}/release-fbc-addon-stage-ocp-${fbc_application_suffix}-${component_application}-${epoch}.yaml
      cp ${template_dir}/release-fbc-addon-stage.yaml ${fbc_addon_release_yaml_path}
      sed -i "s/{{fbc_application}}/${fbc_application_name}/g" ${fbc_addon_release_yaml_path}
      sed -i "s/{{epoch}}/${epoch}/g" ${fbc_addon_release_yaml_path}
      sed -i "s/{{ocp-version}}/ocp-${fbc_application_suffix}/g" ${fbc_addon_release_yaml_path}
      sed -i "s/{{hyphenized-rhoai-version}}/${hyphenized_rhoai_version}/g" ${fbc_addon_release_yaml_path}
      sed -i "s/{{rbc_release_commit}}/${RBC_RELEASE_BRANCH_COMMIT}/g" ${fbc_addon_release_yaml_path}
    fi

    # generate FBC snapshots
    fbc_snapshot_yaml_path=${snapshot_fbc_dir}/snapshot-fbc-stage-ocp-${fbc_application_suffix}-${component_application}-${epoch}.yaml
    cp ${template_dir}/fbc_snapshot.yaml ${fbc_snapshot_yaml_path}
    sed -i "s/{{fbc_application}}/${fbc_application_name}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{ocp-version}}/ocp-${fbc_application_suffix}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{epoch}}/${epoch}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{fbc_fragment_image}}/${FULL_IMAGE_URI_WITH_DIGEST//\//\\/}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{git_url}}/${GIT_URL//\//\\/}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{git_commit}}/${GIT_COMMIT}/g" ${fbc_snapshot_yaml_path}
    sed -i "s/{{rbc_release_commit}}/${RBC_RELEASE_BRANCH_COMMIT}/g" ${fbc_snapshot_yaml_path}

  done < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
echo "all FBC images tag are matching!"


#RBC_RELEASE_BRANCH_COMMIT=7da42450e089babe0dc31f182e78152c349f201a
echo "starting to create the artifacts correnponding to the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"
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

RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH=${workspace}/konflux_components.txt

kubectl get components -o=jsonpath="{range .items[?(@.spec.application=='${component_application}')]}{@.metadata.name}{'\t'}{@.spec.containerImage}{'\n'}{end}" > ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH}




RHOAI_QUAY_API_TOKEN=${RHOAI_QUAY_API_TOKEN} python release_processor.py --operation generate-release-artifacts --catalog-yaml-path ${CATALOG_YAML_PATH} --konflux-components-details-file-path ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH} --rhoai-version ${rhoai_version} --rhoai-application ${component_application} --epoch ${epoch} --output-dir ${release_artifacts_dir} --template-dir ${template_dir} --rbc-release-commit ${RBC_RELEASE_BRANCH_COMMIT}

