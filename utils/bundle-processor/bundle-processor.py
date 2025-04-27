import sys
from datetime import datetime
from pathlib import Path
import subprocess
from jsonupdate_ng import jsonupdate_ng
import requests
import argparse
import yaml
import ruamel.yaml as ruyaml
import os
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

import json
class bundle_processor:
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    OPERATOR_NAME = 'rhods-operator'
    GIT_URL_LABEL_KEY = 'git.url'
    GIT_COMMIT_LABEL_KEY = 'git.commit'

    def __init__(self, build_config_path:str, bundle_csv_path:str, patch_yaml_path:str, rhoai_version:str, output_file_path:str, annotation_yaml_path:str, push_pipeline_operation:str, push_pipeline_yaml_path:str, build_type:str):
        self.build_config_path = build_config_path
        self.bundle_csv_path = bundle_csv_path
        self.patch_yaml_path = patch_yaml_path
        # self.snapshot_json_path = snapshot_json_path
        self.output_file_path = output_file_path
        self.csv_dict = self.parse_csv_yaml()
        self.patch_dict = self.parse_patch_yaml()
        self.build_config = yaml.safe_load(open(self.build_config_path))
        self.rhoai_version = rhoai_version
        self.annotation_yaml_path = annotation_yaml_path
        self.annotation_dict = yaml.safe_load(open(self.annotation_yaml_path))
        self.push_pipeline_operation = push_pipeline_operation
        self.push_pipeline_yaml_path = push_pipeline_yaml_path
        self.push_pipeline_dict = ruyaml.load(open(self.push_pipeline_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        self.build_args_file_path = f'{Path(self.patch_yaml_path).parent}/bundle_build_args.map'
        self.git_meta = ""
        self.build_type = build_type

    def parse_csv_yaml(self):
        # csv_dict = yaml.safe_load(open(self.bundle_csv_path))
        csv_dict = ruyaml.load(open(self.bundle_csv_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        return csv_dict


    def parse_patch_yaml(self):
        return yaml.safe_load(open(self.patch_yaml_path))
    def patch_bundle_csv(self):
        self.latest_images, self.git_labels_meta = [], {}

        self.latest_images, self.git_labels_meta = self.get_all_latest_images_using_bundle_patch()
        self.apply_replacements_to_related_images()

        ODH_OPERATOR_IMAGE = [image['value'] for image in self.latest_images if image['name'] == f'RELATED_IMAGE_ODH_OPERATOR_IMAGE']
        if ODH_OPERATOR_IMAGE:
            self.csv_dict['metadata']['annotations']['containerImage'] = DoubleQuotedScalarString(ODH_OPERATOR_IMAGE[0])
            self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'image'] = DoubleQuotedScalarString(ODH_OPERATOR_IMAGE[0])

        self.latest_images = self.get_latest_images_from_operands_map()
        self.apply_replacements_to_related_images()

        self.latest_images = [image for image in self.latest_images if
                              'FBC' not in image['name'] and 'BUNDLE' not in image['name'] and 'ODH_OPERATOR' not in
                              image['name']]
        self.patch_additional_csv_fields()

        if self.latest_images:
            self.patch_related_images()

        self.process_annotation_yaml()

        self.process_push_pipeline()

        self.write_output_files()

    def get_latest_images_from_operands_map(self):
        #execute shell script to checkout the rhods-operator repo with the given git.commit
        currentDir = Path(os.path.abspath(__file__)).parent
        shellScriptPath = f'{currentDir}/./checkout-rhods-operator.sh'

        if "odh-rhel9-operator" in self.git_labels_meta["map"]:
            operator_name = "odh-rhel9-operator"
        elif "odh-rhel8-operator" in self.git_labels_meta["map"]:
            operator_name = "odh-rhel8-operator"

        git_url = self.git_labels_meta["map"][operator_name][self.GIT_URL_LABEL_KEY]
        git_commit = self.git_labels_meta["map"][operator_name][self.GIT_COMMIT_LABEL_KEY]
        self.git_meta += f'{operator_name.replace("-", "_").upper()}_{self.GIT_URL_LABEL_KEY.replace(".", "_").upper()}={git_url}\n'
        self.git_meta += f'{operator_name.replace("-", "_").upper()}_{self.GIT_COMMIT_LABEL_KEY.replace(".", "_").upper()}={git_commit}\n'
        #self.git_meta += f'{operator_name}.{self.GIT_URL_LABEL_KEY}="${{{{ {operator_name.replace("-", "_").upper()}_{self.GIT_URL_LABEL_KEY.replace(".", "_").upper()} }}}}" \\\n'
        #self.git_meta += f'{operator_name}.{self.GIT_COMMIT_LABEL_KEY}="${{{{ {operator_name.replace("-", "_").upper()}_{self.GIT_COMMIT_LABEL_KEY.replace(".", "_").upper()} }}}}" \\\n'
        # odh-dashboard.git.commit="${CI_ODH_DASHBOARD_UPSTREAM_COMMIT}" \
        dest = f'{currentDir}/rhods-operator'
        self.executeShellScript(f'{shellScriptPath} "{git_url}" {git_commit} {self.rhoai_version} {dest}')



        operands_map_path = f'{dest}/build/operands-map.yaml'


        latest_images = ruyaml.load(open(operands_map_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)

        images = []

        keys = ['RELATED_IMAGE_ODH_OPERATOR_IMAGE']
        for index, image in enumerate(latest_images['relatedImages']):
            if 'name' in image and image['name'] not in keys:
                keys.append(image['name'])
                images.append({'name': image['name'], 'value': image['value']})
            # else:
            #     latest_images['relatedImages'].remove(image)

        self.generate_bundle_build_args()

        return images


    def generate_bundle_build_args(self):
        currentDir = Path(os.path.abspath(__file__)).parent
        dest = f'{currentDir}/rhods-operator'
        self.manifest_config_path = f'{dest}/build/manifests-config.yaml'
        self.manifest_config_dict = yaml.safe_load(open(self.manifest_config_path))

        for component, git_meta in {**self.manifest_config_dict['map'], **self.manifest_config_dict['additional_meta']}.items():
            if 'ref_type' not in git_meta:
                self.git_meta += f'{component.replace("-", "_").upper()}_{self.GIT_URL_LABEL_KEY.replace(".", "_").upper()}={git_meta[self.GIT_URL_LABEL_KEY]}\n'
                self.git_meta += f'{component.replace("-", "_").upper()}_{self.GIT_COMMIT_LABEL_KEY.replace(".", "_").upper()}={git_meta[self.GIT_COMMIT_LABEL_KEY]}\n'
                # self.git_meta += f'{component}.{self.GIT_URL_LABEL_KEY}="${{{component.replace("-", "_").upper()}_{self.GIT_URL_LABEL_KEY.replace(".", "_").upper()}}}" \\\n'
                # self.git_meta += f'{component}.{self.GIT_COMMIT_LABEL_KEY}="${{{component.replace("-", "_").upper()}_{self.GIT_COMMIT_LABEL_KEY.replace(".", "_").upper()}}}" \\\n'
        with open(self.build_args_file_path, "w") as f:
            f.write(self.git_meta)


    def executeShellScript(self, command):
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
        process.wait()
        returnCode = process.returncode
        print(f'Shell script completed this exit code {returnCode}')
        if returnCode > 0:
            sys.exit(returnCode)
        else:
            return returnCode



    def process_push_pipeline(self):
        current_on_cel_expr = self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression']
        disable_ext = 'non-existent-file.non-existent-ext'
        disable_expr = f'"{disable_ext}".pathChanged() && '
        updated=False
        if self.push_pipeline_operation.lower() == 'enable' and disable_ext in current_on_cel_expr:
            self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression'] = current_on_cel_expr.replace(disable_expr, '')
            updated = True
        elif self.push_pipeline_operation.lower() == 'disable' and disable_ext not in current_on_cel_expr:
            self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression'] = f'{disable_expr}{current_on_cel_expr}'
            updated = True

        if updated:
            ruyaml.dump(self.push_pipeline_dict, open(self.push_pipeline_yaml_path, 'w'), Dumper=ruyaml.RoundTripDumper,
                    default_flow_style=False)

    def patch_additional_csv_fields(self):
        self.csv_dict['metadata']['annotations']['createdAt'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.csv_dict['metadata']['name'] = f'{self.OPERATOR_NAME}.{self.patch_dict["patch"]["version"]}'
        self.csv_dict['spec']['version'] = DoubleQuotedScalarString(self.patch_dict["patch"]["version"])


        #remove skip-range and replaces if present
        self.csv_dict['metadata']['annotations'].pop('olm.skipRange', None)
        self.csv_dict['spec'].pop('replaces', None)

        #sync csv-patch
        csv_patch_file = self.patch_dict['patch']['additional-fields']['file']
        csv_patch_dict = yaml.safe_load(open(f'{Path(self.patch_yaml_path).parent.absolute()}/{csv_patch_file}'))

        self.csv_dict = jsonupdate_ng.updateJson(self.csv_dict, csv_patch_dict)




    def process_annotation_yaml(self):
        self.annotation_dict['annotations'].pop('operators.operatorframework.io.bundle.channels.v1', None)
        self.annotation_dict['annotations'].pop('operators.operatorframework.io.bundle.channel.default.v1', None)

    def write_output_files(self):
        # docs = [self.csv_dict]
        # yaml.add_representer(str, str_presenter)
        # yaml.representer.SafeRepresenter.add_representer(str, str_presenter)
        # yaml.safe_dump_all(docs, open(self.output_file_path, 'w'), sort_keys=False)
        ruyaml.dump(self.csv_dict, open(self.output_file_path, 'w'), Dumper=ruyaml.RoundTripDumper, default_flow_style=False)
        yaml.safe_dump(self.annotation_dict, open(self.annotation_yaml_path, 'w'), sort_keys=False)

    def patch_related_images(self):
        SCHEMA = 'relatedImages'
        PATCH_SCHEMA = 'olm.channels'
        env_list = self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'env']
        env_list = [dict(item) for item in env_list]
        env_object = jsonupdate_ng.updateJson({'env': env_list}, {'env': self.latest_images}, meta={'listPatchScheme': {'$.env': {'key': 'name'}}}) #, 'keyType': 'partial', 'keySeparator': '@'
        self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
            'env'] = env_object['env']
        relatedImages = []
        for name, value in self.csv_dict['metadata']['annotations'].items():
            if value.startswith(self.PRODUCTION_REGISTRY) and '@sha256:' in value:
                relatedImages.append({'name': f'{value.split("/")[-1].replace("@sha256:", "-")}-annotation', 'image': value})
        relatedImages += [{'name': image['name'].replace('RELATED_IMAGE_', '').lower(), 'image': image['value']} for image in self.latest_images]
        self.csv_dict['spec']['relatedImages'] = relatedImages

    def apply_replacements_to_related_images(self):
        for relatedImage in self.latest_images:
            relatedImage['value'] = self.apply_replacement(relatedImage['value'])

    def apply_replacement(self, value:str):
        if value:
            for replacement in self.build_config['config']['replacements']:
                intermediate_registry = replacement['registry']
                for old, new in replacement['repo_mappings'].items():
                    value = value.replace(f'{intermediate_registry}/{old}@', f'{self.PRODUCTION_REGISTRY}/{new}@')
        return value
    def get_all_latest_images(self):
        latest_images = []
        qc = quay_controller('rhoai')
        for registry_entry in self.build_config['config']['replacements']:
            registry = registry_entry['registry']
            for repo_path in registry_entry['repo_mappings']:
                repo = '/'.join(repo_path.split('/')[1:])
                tags = qc.get_all_tags(repo, self.rhoai_version)
                if not tags:
                    print(f'no tags found for {repo}')
                for tag in tags:
                    sig_tag = f'{tag["manifest_digest"].replace(":", "-")}.sig'
                    signature = qc.get_tag_details(repo, sig_tag)
                    if signature:
                        latest_images.append({'name': f'RELATED_IMAGE_{repo.replace("-rhel8", "").replace("-rhel9", "").replace("-", "_").upper()}_IMAGE', 'value': DoubleQuotedScalarString(f'{registry}/{repo_path}@{tag["manifest_digest"]}')})
                        break
        print('latest_images', json.dumps(latest_images, indent=4))
        return latest_images

    def get_all_latest_images_using_bundle_patch(self):
        latest_images = []
        missing_images = []
        git_labels_meta = {'map': {}}

        for image_entry in [image for image in self.patch_dict['patch']['relatedImages'] if image['name'] == 'RELATED_IMAGE_ODH_OPERATOR_IMAGE']:
            parts = image_entry['value'].split('@')[0].split('/')
            registry = parts[0]
            org = parts[1]
            qc = quay_controller(org)
            repo = '/'.join(parts[2:])
            version_tag = f'{self.rhoai_version}-nightly' if self.build_type.lower() == 'nightly' else self.rhoai_version
            tags = qc.get_all_tags(repo, version_tag)
            component_name = repo.replace('-rhel8', '').replace('-rhel9', '') if repo.endswith(('-rhel8', '-rhel9')) else repo

            if not tags:
                print(f'no tags found for {repo}')
                missing_images.append(repo)
            for tag in tags:
                sig_tag = f'{tag["manifest_digest"].replace(":", "-")}.sig'
                signature = qc.get_tag_details(repo, sig_tag)
                if signature:
                    value = f'{registry}/{org}/{repo}@{tag["manifest_digest"]}'
                    # if image_entry['value'] != value:
                    image_entry['value'] = DoubleQuotedScalarString(value)
                    latest_images.append(image_entry)

                    manifest_digest = tag["manifest_digest"]
                    if tag['is_manifest_list'] == True:
                        image_manifest_digests = qc.get_image_manifest_digests_for_all_the_supported_archs(repo, manifest_digest)
                        if image_manifest_digests:
                            manifest_digest = image_manifest_digests[0]


                    labels = qc.get_git_labels(repo, manifest_digest)
                    labels = {label['key']:label['value'] for label in labels if label['value']}
                    git_url = labels[self.GIT_URL_LABEL_KEY]
                    git_commit = labels[self.GIT_COMMIT_LABEL_KEY]
                    git_labels_meta['map'][component_name] = {}
                    git_labels_meta['map'][component_name][self.GIT_URL_LABEL_KEY] = git_url
                    git_labels_meta['map'][component_name][self.GIT_COMMIT_LABEL_KEY] = git_commit

                    break
        if missing_images:
            print('Images missing for following components : ', missing_images)
            sys.exit(1)
        print('latest_images', json.dumps(latest_images, indent=4))
        return latest_images, git_labels_meta



def str_presenter(dumper, data):
    if data.count('\n') > 0:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    # if '"' in data:
    #     return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

class snapshot_processor:
    def __init__(self, snapshot_json_path:str, output_file_path:str, image_filter:str=''):
        self.snapshot_json_path = snapshot_json_path
        self.output_file_path = output_file_path
        self.image_filter = image_filter

    def extract_images_from_snapshot(self):
        snapshot = json.load(open(self.snapshot_json_path))
        output_images = []
        for component in snapshot['spec']['components']:
            if 'bundle' not in component['name'] and 'fbc' not in component['name'] and 'odh-operator' not in component['name']:
                output_images.append({'name': f'RELATED_IMAGE_{component["name"].upper().split("-V2")[0].replace("-", "_")}_IMAGE', 'value': DoubleQuotedScalarString(component["containerImage"])})

        return output_images

BASE_URL = 'https://quay.io/api/v1'
class quay_controller:
    def __init__(self, org:str):
        self.org = org
    def get_tag_details(self, repo, tag):
        result_tag = {}
        url = f'{BASE_URL}/repository/{self.org}/{repo}/tag/?specificTag={tag}&onlyActiveTags=true'
        headers = {'Authorization': f'Bearer {os.environ[self.org.upper() + "_QUAY_API_TOKEN"]}',
                   'Accept': 'application/json'}
        response = requests.get(url, headers=headers)
        tags = response.json()['tags']
        if tags:
            result_tag = tags[0]
        return result_tag
    def get_all_tags(self, repo, tag):
        url = f'{BASE_URL}/repository/{self.org}/{repo}/tag/?specificTag={tag}&onlyActiveTags=false'
        headers = {'Authorization': f'Bearer {os.environ[self.org.upper() + "_QUAY_API_TOKEN"]}',
                   'Accept': 'application/json'}
        response = requests.get(url, headers=headers)
        if 'tags' in response.json():
            tag = response.json()['tags']
            return tag
        else:
            print(response.json())
            sys.exit(1)

    def get_supported_archs(self, repo, manifest_digest):
        manifest_json = self.get_manifest_details(repo, manifest_digest)
        supported_archs = []
        if manifest_json['is_manifest_list'] == True:
            manifest_data = manifest_json['manifest_data']
            manifest_data = json.loads(manifest_data)
            for manifest in manifest_data['manifests']:
                supported_archs.append(f'{manifest["platform"]["os"]}-{manifest["platform"]["architecture"]}')
        return supported_archs

    def get_image_manifest_digests_for_all_the_supported_archs(self, repo, manifest_digest):
        manifest_json = self.get_manifest_details(repo, manifest_digest)
        image_manifest_digests = []
        if manifest_json['is_manifest_list'] == True:
            manifest_data = manifest_json['manifest_data']
            manifest_data = json.loads(manifest_data)
            for manifest in manifest_data['manifests']:
                image_manifest_digests.append(manifest['digest'])
        return image_manifest_digests

    def get_manifest_details(self, repo, manifest_digest):
        url = f'{BASE_URL}/repository/{self.org}/{repo}/manifest/{manifest_digest}'
        headers = {'Authorization': f'Bearer {os.environ[self.org.upper() + "_QUAY_API_TOKEN"]}',
                   'Accept': 'application/json'}
        response = requests.get(url, headers=headers)

        if 'manifest_data' in response.json():
            return response.json()
        else:
            print(response.json())
            sys.exit(1)

    def get_git_labels(self, repo, tag):
        url = f'{BASE_URL}/repository/{self.org}/{repo}/manifest/{tag}/labels?filter=git'
        headers = {'Authorization': f'Bearer {os.environ[self.org.upper() + "_QUAY_API_TOKEN"]}',
                   'Accept': 'application/json'}
        response = requests.get(url, headers=headers)
        if 'labels' in response.json():
            labels = response.json()['labels']
            return labels
        else:
            print(response.json())
            sys.exit(1)
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "bundle-patch"', dest='operation')
    parser.add_argument('-b', '--build-config-path', required=False,
                        help='Path of the build-config.yaml', dest='build_config_path')
    parser.add_argument('-t', '--build-type', required=False,
                        help='Path of the build-config.yaml', dest='build_type', default='ci')
    parser.add_argument('-c', '--bundle-csv-path', required=False,
                        help='Path of the bundle csv yaml from the release branch.', dest='bundle_csv_path')
    parser.add_argument('-p', '--patch-yaml-path', required=False,
                        help='Path of the bundle-patch.yaml from the release branch.', dest='patch_yaml_path')
    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the output bundle csv', dest='output_file_path')
    parser.add_argument('-sn', '--snapshot-json-path', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='snapshot_json_path')
    parser.add_argument('-f', '--image-filter', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='image_filter')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    parser.add_argument('-a', '--annotation-yaml-path', required=False,
                        help='Path of the annotation.yaml from the raw inputs', dest='annotation_yaml_path')
    parser.add_argument('-y', '--push-pipeline-yaml-path', required=False,
                        help='Path of the tekton pipeline for push builds', dest='push_pipeline_yaml_path')
    parser.add_argument('-x', '--push-pipeline-operation', required=False, default="enable",
                        help='Operation code, supported values are "enable" and "disable"', dest='push_pipeline_operation')
    args = parser.parse_args()

    if args.operation.lower() == 'bundle-patch':
        processor = bundle_processor(build_config_path=args.build_config_path, bundle_csv_path=args.bundle_csv_path, patch_yaml_path=args.patch_yaml_path, rhoai_version=args.rhoai_version, output_file_path=args.output_file_path, annotation_yaml_path=args.annotation_yaml_path, push_pipeline_operation=args.push_pipeline_operation, push_pipeline_yaml_path=args.push_pipeline_yaml_path, build_type=args.build_type)
        processor.patch_bundle_csv()

    # build_config_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/config/build-config.yaml'
    # bundle_csv_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/to-be-processed/bundle/manifests/rhods-operator.clusterserviceversion.yaml'
    # patch_yaml_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/bundle/bundle-patch.yaml'
    # annotation_yaml_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/to-be-processed/bundle/metadata/annotations.yaml'
    # output_file_path = 'output.yaml'
    # rhoai_version = 'rhoai-2.13'
    # push_pipeline_operation = 'enable'
    # push_pipeline_yaml_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/.tekton/odh-operator-bundle-v2-13-push.yaml'
    #
    #
    # processor = bundle_processor(build_config_path=build_config_path, bundle_csv_path=bundle_csv_path,
    #                              patch_yaml_path=patch_yaml_path, rhoai_version=rhoai_version,
    #                              output_file_path=output_file_path, annotation_yaml_path=annotation_yaml_path,
    #                              push_pipeline_yaml_path=push_pipeline_yaml_path, push_pipeline_operation=push_pipeline_operation)
    # processor.patch_bundle_csv()


