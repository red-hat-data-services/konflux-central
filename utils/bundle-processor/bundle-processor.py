
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
    def __init__(self, build_config_path:str, bundle_csv_path:str, patch_yaml_path:str, rhoai_version:str, output_file_path:str):
        self.build_config_path = build_config_path
        self.bundle_csv_path = bundle_csv_path
        self.patch_yaml_path = patch_yaml_path
        # self.snapshot_json_path = snapshot_json_path
        self.output_file_path = output_file_path
        self.csv_dict = self.parse_csv_yaml()
        self.patch_dict = self.parse_patch_yaml()
        self.build_config = yaml.safe_load(open(self.build_config_path))
        self.rhoai_version = rhoai_version

    def parse_csv_yaml(self):
        # csv_dict = yaml.safe_load(open(self.bundle_csv_path))
        csv_dict = ruyaml.load(open(self.bundle_csv_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        return csv_dict


    def parse_patch_yaml(self):
        return yaml.safe_load(open(self.patch_yaml_path))
    def patch_bundle_csv(self):
        # processor = snapshot_processor(snapshot_json_path=self.snapshot_json_path, output_file_path=None)
        # self.latest_images = processor.extract_images_from_snapshot()
        self.latest_images = self.get_all_latest_images()
        self.apply_replacements_to_related_images()
        ODH_OPERATOR_IMAGE = [image['value'] for image in self.latest_images if image['name'] == f'RELATED_IMAGE_ODH_OPERATOR_IMAGE']
        self.latest_images = [image for image in self.latest_images if 'FBC' not in image['name'] or 'BUNDLE' not in image['name'] or 'ODH_OPERATOR' not in image['name'] ]
        if ODH_OPERATOR_IMAGE:
            self.csv_dict['metadata']['annotations']['containerImage'] = DoubleQuotedScalarString(ODH_OPERATOR_IMAGE[0])
        if self.latest_images:
            self.patch_related_images()

        self.write_output_catalog()

    def write_output_catalog(self):
        # docs = [self.csv_dict]
        # yaml.add_representer(str, str_presenter)
        # yaml.representer.SafeRepresenter.add_representer(str, str_presenter)
        # yaml.safe_dump_all(docs, open(self.output_file_path, 'w'), sort_keys=False)
        ruyaml.dump(self.csv_dict, open(self.output_file_path, 'w'), Dumper=ruyaml.RoundTripDumper, default_flow_style=False)

    def patch_related_images(self):
        SCHEMA = 'relatedImages'
        PATCH_SCHEMA = 'olm.channels'
        env_list = self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'env']
        env_list = [dict(item) for item in env_list]
        env_object = jsonupdate_ng.updateJson({'env': env_list}, {'env': self.latest_images}, meta={'listPatchScheme': {'$.env': 'name'}})
        self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
            'env'] = env_object['env']

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
                        latest_images.append({'name': f'RELATED_IMAGE_{repo.replace("-rhel8", "").replace("-", "_").upper()}_IMAGE', 'value': DoubleQuotedScalarString(f'{registry}/{repo_path}@{tag["manifest_digest"]}')})
                        break
        print('latest_images', json.dumps(latest_images, indent=4))
        return latest_images

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
        tag = response.json()['tags']
        return tag

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "bundle-patch"', dest='operation')
    parser.add_argument('-b', '--build-config-path', required=False,
                        help='Path of the build-config.yaml', dest='build_config_path')
    parser.add_argument('-c', '--bundle-csv-path', required=False,
                        help='Path of the bundle csv yaml from the release branch.', dest='bundle_csv_path')
    parser.add_argument('-p', '--patch-yaml-path', required=False,
                        help='Path of the bundle-patch.yaml from the release branch.', dest='patch_yaml_path')
    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='output_file_path')
    parser.add_argument('-sn', '--snapshot-json-path', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='snapshot_json_path')
    parser.add_argument('-f', '--image-filter', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='image_filter')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    args = parser.parse_args()

    if args.operation.lower() == 'bundle-patch':
        processor = bundle_processor(build_config_path=args.build_config_path, bundle_csv_path=args.bundle_csv_path, patch_yaml_path=args.patch_yaml_path, rhoai_version=args.rhoai_version, output_file_path=args.output_file_path)
        processor.patch_bundle_csv()

    # build_config_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/config/build-config.yaml'
    # bundle_csv_path = '/home/dchouras/RHODS/DevOps/FBC/rhoai-2.13/bundle/manifests/rhods-operator.clusterserviceversion.yml'
    # patch_yaml_path = '/home/dchouras/RHODS/DevOps/FBC/rhoai-2.13/bundle/bundle-patch.yaml'
    # snapshot_json_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/config/snapshot.json'
    # output_file_path = 'output.yaml'
    # rhoai_version = 'rhoai-2.13'
    #
    # processor = bundle_processor(build_config_path=build_config_path, bundle_csv_path=bundle_csv_path,
    #                              patch_yaml_path=patch_yaml_path, rhoai_version=rhoai_version,
    #                              output_file_path=output_file_path)
    # processor.patch_bundle_csv()


