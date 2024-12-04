import argparse
import json
import sys
import os
import requests

import yaml
import ruamel.yaml as ruyaml
from collections import defaultdict
class release_processor:
    OPERATOR_NAME = 'rhods-operator'
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    DEV_REGISTRY = 'quay.io'
    RHOAI_NAMESPACE = 'rhoai'
    GIT_URL_LABEL_KEY = 'git.url'
    GIT_COMMIT_LABEL_KEY = 'git.commit'
    def __init__(self, catalog_yaml_path:str, konflux_components_details_file_path:str, rhoai_version:str, output_dir:str, rhoai_application:str, epoch, template_dir:str, rbc_release_commit:str, snapshot_file_path:str=''):
        self.catalog_yaml_path = catalog_yaml_path
        self.catalog_dict:defaultdict = self.parse_catalog_yaml()
        self.konflux_components_details_file_path = konflux_components_details_file_path
        self.rhoai_version = rhoai_version
        self.output_dir = output_dir
        self.release_components_dir = f'{self.output_dir}/release-components'
        self.snapshot_components_dir = f'{self.output_dir}/snapshot-components'
        self.current_operator = f'{self.OPERATOR_NAME}.{self.rhoai_version}'
        self.konflux_components = self.parse_konflux_components_details()
        self.rhoai_application = rhoai_application
        self.epoch = str(epoch)
        self.template_dir = template_dir
        self.hyphenized_rhoai_version = self.rhoai_application.replace('rhoai-', '')
        self.rbc_release_commit = rbc_release_commit
        self.replacements = {'component_application': self.rhoai_application, 'epoch': self.epoch, 'hyphenized-rhoai-version':self.hyphenized_rhoai_version, 'rbc_release_commit': self.rbc_release_commit }
        self.snapshot_file_path = snapshot_file_path


    def validate_snapshot_with_catalog(self):
        snapshot_dict = yaml.safe_load(open(self.snapshot_file_path))
        snapshot_components = snapshot_dict['spec']['components']
        catalog_images = self.catalog_dict['olm.bundle'][self.current_operator]['relatedImages']
        konflux_components = {name:repo for repo, name in self.konflux_components.items()}
        self.extract_rhoai_images_from_catalog()

        for component in snapshot_components:
            component_name = component['name']
            if not component['containerImage'].startswith(konflux_components[component_name]):
                print(f'quay repo {konflux_components[component_name]} does not belong to the konflux component {component_name}..exiting')
                sys.exit(1)
            else:
                print(f'quay repo {konflux_components[component_name]} matches with the konflux component {component_name}!')

            if component['containerImage'] not in self.expected_rhoai_images:
                print(f'snapshot image not found in catalog - {component['containerImage']}')
            else:
                print(f'snapshot image found in catalog - {component['containerImage']}!')





    def parse_catalog_yaml(self):
        # objs = yaml.safe_load_all(open(self.catalog_yaml_path))
        objs = ruyaml.load_all(open(self.catalog_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        catalog_dict = defaultdict(dict)
        for obj in objs:
            catalog_dict[obj['schema']][obj['name']] = obj
        return catalog_dict

    def parse_konflux_components_details(self):
        konflux_components = {}
        components_details = open(self.konflux_components_details_file_path).readlines()
        for entry in components_details:
            if entry:
                parts = entry.split('\t')
                component_name = parts[0]
                component_repo = parts[1].split('@')[0]
                if 'fbc' not in component_name:
                    konflux_components[component_repo] = component_name

        return konflux_components


    def extract_rhoai_images_from_catalog(self):
        self.expected_rhoai_images = [image['image'] for image in self.catalog_dict['olm.bundle'][self.current_operator]['relatedImages'] if f'{self.PRODUCTION_REGISTRY}/{self.RHOAI_NAMESPACE}/' in image['image']]
        self.expected_rhoai_images = [image.replace(f'{self.PRODUCTION_REGISTRY}/{self.RHOAI_NAMESPACE}/', f'{self.DEV_REGISTRY}/{self.RHOAI_NAMESPACE}/') for image in self.expected_rhoai_images]
        # json.dump(expected_rhoai_images, open(self.output_file_path, 'w'), indent=4)

    def generate_release_artifacts(self):
        self.extract_rhoai_images_from_catalog()
        self.generate_component_snapshot()
        self.generate_component_release()

    def generate_component_snapshot(self):
        snapshot_components = []
        for image in self.expected_rhoai_images:
            snapshot_component = {}
            image_parts = image.split('@')
            repo_path = image_parts[0]
            manifest_digest = image_parts[1]
            parts = repo_path.split('/')
            registry = parts[0]
            org = parts[1]
            repo = '/'.join(parts[2:])
            qc = quay_controller(org)
            sig_tag = f'{manifest_digest.replace(":", "-")}.sig'
            signature = qc.get_tag_details(repo, sig_tag)
            # signature=True
            if signature:
                labels = qc.get_git_labels(repo, manifest_digest)
                labels = {label['key']: label['value'] for label in labels if label['value']}
                git_url = labels[self.GIT_URL_LABEL_KEY]
                git_commit = labels[self.GIT_COMMIT_LABEL_KEY]
                snapshot_component['name'] = self.konflux_components[repo_path]
                snapshot_component['containerImage'] = image
                snapshot_component['source'] = {'git': {}}
                snapshot_component['source']['git']['url'] = git_url
                snapshot_component['source']['git']['revision'] = git_commit

                snapshot_components.append(snapshot_component)
            else:
                print(f'Invalid image, could not verify signature of {image}')
                sys.exit(1)

        component_snapshot = open(f'{self.template_dir}/component_snapshot.yaml').read()
        for key, value in self.replacements.items():
            component_snapshot = component_snapshot.replace(f'{{{{{key}}}}}', value)

        component_snapshot = yaml.safe_load(component_snapshot)
        component_snapshot['spec']['components'] = snapshot_components

        yaml.safe_dump(component_snapshot, open(f'{self.snapshot_components_dir}/snapshot-components-stage-{self.rhoai_application}-{self.epoch}.yaml', 'w'))


    def generate_component_release(self):
        component_release = open(f'{self.template_dir}/release-components-stage.yaml').read()
        for key, value in self.replacements.items():
            component_release = component_release.replace(f'{{{{{key}}}}}', value)

        component_release = yaml.safe_load(component_release)
        yaml.safe_dump(component_release, open(f'{self.release_components_dir}/release-components-stage-{self.rhoai_application}-{self.epoch}.yaml', 'w'))




class snapshot_processor:
    def __init__(self, snapshot_file_path:str, expected_rhoai_images_file_path:str, snapshot_name:str):
        self.snapshot_file_path = snapshot_file_path
        self.expected_rhoai_images_file_path = expected_rhoai_images_file_path
        self.snaphot_images = json.load(open(self.snapshot_file_path))
        self.expected_rhoai_images = json.load(open(self.expected_rhoai_images_file_path))
        self.snapshot_name = snapshot_name
    def check_snapshot_compatibility(self):
        self.snaphot_images = self.snaphot_images['images'] if 'images' in self.snaphot_images else self.snaphot_images
        self.snaphot_images = [image for image in self.snaphot_images if 'rhoai-fbc-fragment' not in image]
        result = {'snapshot_name': self.snapshot_name, 'compatible': 'NO', 'images': self.snaphot_images}
        if set(self.snaphot_images) == set(self.expected_rhoai_images):
            result['compatible'] = 'YES'
        json.dump(result, open(self.snapshot_file_path, 'w'), indent=4)

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
                        help='Operation code, supported values are "generate-release-artifacts", "validate-snapshot-with-catalog", "extract-rhoai-images-from-catalog" and "check-snapshot-compatibility"',
                        dest='operation')
    parser.add_argument('-c', '--catalog-yaml-path', required=False,
                        help='Path of the catalog.yaml from the current catalog.', dest='catalog_yaml_path')
    parser.add_argument('-k', '--konflux-components-details-file-path', required=False,
                        help='Path of the yaml with details of all the konflux components for current version.', dest='konflux_components_details_file_path')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    parser.add_argument('-a', '--rhoai-application', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_application')
    parser.add_argument('-ep', '--epoch', required=False,
                        help='centrally generated epoch to be used with all the artifacts', dest='epoch')
    parser.add_argument('-t', '--template-dir', required=False,
                        help='Dir with all the template artifacts', dest='template_dir')
    parser.add_argument('-r', '--rbc-release-commit', required=False,
                        help='Dir with all the template artifacts', dest='rbc_release_commit')


    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the output images yaml', dest='output_file_path')
    parser.add_argument('-of', '--output-dir', required=False,
                        help='Path to generate all the release artifacts', dest='output_dir')
    parser.add_argument('-s', '--snapshot-file-path', required=False,
                        help='Path of the snapshot yaml', dest='snapshot_file_path')
    parser.add_argument('-n', '--snapshot-name', required=False,
                        help='Path of the snapshot yaml', dest='snapshot_name')
    parser.add_argument('-e', '--expected-rhoai-images-file-path', required=False,
                        help='expected rhoai images in the catalog yaml', dest='expected_rhoai_images_file_path')

    args = parser.parse_args()

    if args.operation.lower() == 'generate-release-artifacts':
        processor = release_processor(catalog_yaml_path=args.catalog_yaml_path, konflux_components_details_file_path=args.konflux_components_details_file_path, rhoai_version=args.rhoai_version, output_dir=args.output_dir, rhoai_application=args.rhoai_application, epoch=args.epoch, template_dir=args.template_dir, rbc_release_commit=args.rbc_release_commit)
        processor.generate_release_artifacts()
    elif args.operation.lower() == 'validate-snapshot-with-catalog':
        processor = release_processor(catalog_yaml_path=args.catalog_yaml_path, konflux_components_details_file_path=args.konflux_components_details_file_path, snapshot_file_path=args.snapshot_file_path, rhoai_version=args.rhoai_version, output_dir=None, rhoai_application=args.rhoai_application, epoch='', template_dir=None, rbc_release_commit=None)
        processor.validate_snapshot_with_catalog()


    elif args.operation.lower() == 'extract-rhoai-images-from-catalog':
        processor = release_processor(catalog_yaml_path=args.catalog_yaml_path, rhoai_version=args.rhoai_version, output_file_path=args.output_file_path)
        processor.extract_rhoai_images_from_catalog()
    elif args.operation.lower() == 'check-snapshot-compatibility':
        processor = snapshot_processor(snapshot_file_path=args.snapshot_file_path, expected_rhoai_images_file_path=args.expected_rhoai_images_file_path, snapshot_name=args.snapshot_name)
        processor.check_snapshot_compatibility()
