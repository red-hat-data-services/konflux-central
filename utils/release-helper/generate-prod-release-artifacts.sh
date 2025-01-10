#!/bin/bash
#Prerequisites
# yq
# ~/.ssh/.quay_devops_application_token

release_branch=rhoai-2.16
rhoai_version=2.16.0
hyphenized_rhoai_version=v2-16


RBC_URL=https://github.com/red-hat-data-services/RHOAI-Build-Config
FBC_QUAY_REPO=quay.io/rhoai/rhoai-fbc-fragment
component_application=rhoai-${hyphenized_rhoai_version}

fbc_application_prefix=rhoai-fbc-fragment-ocp-

RHOAI_QUAY_API_TOKEN=$(cat ~/.ssh/.quay_devops_application_token)

current_dir=$(pwd)
#workspace=/tmp/tmp.68Vts9xj27
#epoch=1732618870

workspace=$(mktemp -d)
echo "workspace=${workspace}"

epoch=$(date +%s)
release_artifacts_dir=prod-release-${epoch}
release_components_dir=${release_artifacts_dir}/release-components
release_fbc_dir=${release_artifacts_dir}/release-fbc

mkdir -p ${release_components_dir}
mkdir -p ${release_fbc_dir}

template_dir=templates/prod

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
      echo "Stage FBC images are out of sync, exiting.."
      exit 1
    fi

    snapshot_count=$(oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} --no-headers | grep rhoai-fbc-fragment-ocp-${fbc_application_suffix} | awk '{print $1}' | wc -l)
    if [[ $snapshot_count -eq 1 ]]
    then
      snapshot_name=$(oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} --no-headers | grep rhoai-fbc-fragment-ocp-${fbc_application_suffix} | awk '{print $1}')
      echo "Found the production FBC snapshot for OCP ${ocp_version} to use for the release - ${snapshot_name}"

      snapshot_container_image=$(oc get snapshot $snapshot_name -o=jsonpath='{.spec.components[0].containerImage}')

        echo "snapshot_container_image = $snapshot_container_image"
        echo "Stage_quay_image = $FULL_IMAGE_URI_WITH_DIGEST"

      if [[ "$snapshot_container_image" != "$FULL_IMAGE_URI_WITH_DIGEST" ]]
      then
        echo "Snapshot FBC image doesn't match with the latest quay image on stage for OCP version ${ocp_version}, exiting.. "
        echo "snapshot_container_image = $snapshot_container_image"
        echo "Stage_quay_image = $FULL_IMAGE_URI_WITH_DIGEST"
        exit 1
      fi
      # generate FBC Release CR
      fbc_release_yaml_path=${release_fbc_dir}/prod-release-fbc-ocp-${fbc_application_suffix}-${component_application}-${epoch}.yaml

      #replace variables
      cp ${template_dir}/release-fbc-prod.yaml ${fbc_release_yaml_path}
      sed -i "s/{{fbc_application}}/${fbc_application_name}/g" ${fbc_release_yaml_path}
      sed -i "s/{{epoch}}/${epoch}/g" ${fbc_release_yaml_path}
      sed -i "s/{{ocp-version}}/ocp-${fbc_application_suffix}/g" ${fbc_release_yaml_path}
      sed -i "s/{{hyphenized-rhoai-version}}/${hyphenized_rhoai_version}/g" ${fbc_release_yaml_path}
      sed -i "s/{{rbc_release_commit}}/${RBC_RELEASE_BRANCH_COMMIT}/g" ${fbc_release_yaml_path}

      #replace snapshot
      snapshot_name=${snapshot_name} yq e -i '.spec.snapshot = env(snapshot_name)' ${fbc_release_yaml_path}
      echo "Successfully generated the Release - ${fbc_application_name}-prod-${epoch}"
    elif [[ $snapshot_count -gt 1 ]]
    then
      echo "Found multiple FBC snapshots with the given condition, please cleanup manually and ensure only one eligible snapshot exists ...exiting"
      oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} | grep rhoai-fbc-fragment-ocp-${fbc_application_suffix}
      exit 1
    else
      echo "Could not find any appropriate components-snapshot to promote to production...exiting"
      exit 1
    fi
  done < <(yq eval '.config.supported-ocp-versions.release[]' $BUILD_CONFIG_PATH)
echo "all FBC images tag are matching!"


echo "Verifying if the required components snapshots exists for prod push.."

snapshot_count=$(oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} --no-headers | grep ${component_application} | awk '{print $1}' | wc -l)

components_snapshot_name=
if [[ $snapshot_count -eq 1 ]]
then
  components_snapshot_name=$(oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} --no-headers | grep ${component_application} | awk '{print $1}')
  echo "Found the production snashot to use for the release - ${components_snapshot_name} "
elif [[ $snapshot_count -gt 1 ]]
then
  echo "Found multiple snapshots with the given condition, please cleanup manually and ensure only one eligible snapshot exists ...exiting"
  oc get snapshot -l konflux-release-data/rbc-release-commit=${RBC_RELEASE_BRANCH_COMMIT} | grep ${component_application}
  exit 1
else
  echo "Could not find any appropriate components-snapshot to promote to production...exiting"
  exit 1
fi

#RBC_RELEASE_BRANCH_COMMIT=7da42450e089babe0dc31f182e78152c349f201a
echo "starting to create the artifacts corresponding to the sourcecode at ${RBC_URL}/tree/${RBC_RELEASE_BRANCH_COMMIT}"


RBC_RELEASE_DIR=${workspace}/RBC_${release_branch}_commit
V416_CATALOG_YAML_PATH=catalog/v4.16/rhods-operator/catalog.yaml
CATALOG_YAML_PATH=${RBC_RELEASE_DIR}/${V416_CATALOG_YAML_PATH}

mkdir -p ${RBC_RELEASE_DIR}
cd ${RBC_RELEASE_DIR}
git config --global init.defaultBranch ${release_branch}
git init -q
git remote add origin $RBC_URL
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "${V416_CATALOG_YAML_PATH}" >> .git/info/sparse-checkout
git fetch -q --depth=1 origin ${RBC_RELEASE_BRANCH_COMMIT}
git checkout -q ${RBC_RELEASE_BRANCH_COMMIT}
cd ${current_dir}

RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH=${workspace}/konflux_components.txt
SNAPSHOT_YAML_PATH=${workspace}/${components_snapshot_name}.yaml

oc get snapshot ${components_snapshot_name} -o yaml > ${SNAPSHOT_YAML_PATH}
kubectl get components -o=jsonpath="{range .items[?(@.spec.application=='${component_application}')]}{@.metadata.name}{'\t'}{@.spec.containerImage}{'\n'}{end}" > ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH}

python release_processor.py --operation validate-snapshot-with-catalog --catalog-yaml-path ${CATALOG_YAML_PATH} --konflux-components-details-file-path ${RHOAI_KONFLUX_COMPONENTS_DETAILS_FILE_PATH} --rhoai-version ${rhoai_version} --rhoai-application ${component_application} --snapshot-file-path ${SNAPSHOT_YAML_PATH}

components_release_yaml_path=${release_components_dir}/prod-release-components-${component_application}-${epoch}.yaml

  #replace variables
  cp ${template_dir}/release-components-prod.yaml ${components_release_yaml_path}
  sed -i "s/{{component_application}}/${component_application}/g" ${components_release_yaml_path}
  sed -i "s/{{epoch}}/${epoch}/g" ${components_release_yaml_path}
  sed -i "s/{{hyphenized-rhoai-version}}/${hyphenized_rhoai_version}/g" ${components_release_yaml_path}
  sed -i "s/{{rbc_release_commit}}/${RBC_RELEASE_BRANCH_COMMIT}/g" ${components_release_yaml_path}

  #replace snapshot
  components_snapshot_name=${components_snapshot_name} yq e -i '.spec.snapshot = env(components_snapshot_name)' ${components_release_yaml_path}
  echo "Successfully generated the Release - ${component_application}-prod-${epoch}!!"