import os, requests
import time

from jsonupdate_ng import jsonupdate_ng
import argparse
import yaml
import ruamel.yaml as ruyaml
import json
from collections import defaultdict
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
import base64
import sys

class stage_promoter:
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    PACKAGE_NAME = 'rhods-operator'

    def __init__(self, catalog_yaml_path:str, patch_yaml_path:str, release_catalog_yaml_path:str, output_file_path:str, rhoai_version:str):
        self.catalog_yaml_path = catalog_yaml_path
        self.patch_yaml_path = patch_yaml_path
        self.release_catalog_yaml_path = release_catalog_yaml_path
        self.output_file_path = output_file_path
        self.catalog_dict:defaultdict = self.parse_catalog_yaml()
        self.patch_dict = self.parse_patch_yaml()
        self.rhoai_version = rhoai_version
        self.current_bundle_name = f'{self.PACKAGE_NAME}.{self.rhoai_version.lower().strip("v")}'

    def parse_catalog_yaml(self):
        # objs = yaml.safe_load_all(open(self.catalog_yaml_path))
        objs = ruyaml.load_all(open(self.catalog_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        print(type(objs))
        catalog_dict = defaultdict(dict)
        for obj in objs:
            catalog_dict[obj['schema']][obj['name']] = obj
        return catalog_dict

    def patch_current_release_bundle_schema(self):
        objs = yaml.safe_load_all(open(self.release_catalog_yaml_path))
        release_catalog_dict = defaultdict(dict)
        BUNDLE_SCHEMA = 'olm.bundle'
        for obj in objs:
            release_catalog_dict[obj['schema']][obj['name']] = obj
        current_release_bundle_schema = [olm_bundle for name, olm_bundle in release_catalog_dict[BUNDLE_SCHEMA].items() if name == self.current_bundle_name ]
        if current_release_bundle_schema and len(current_release_bundle_schema) == 1:
            current_release_bundle_schema = current_release_bundle_schema[0]
            self.catalog_dict[BUNDLE_SCHEMA][current_release_bundle_schema['name']] = current_release_bundle_schema
        elif not current_release_bundle_schema:
            raise Exception(f'No olm.bundle schema found for {self.current_bundle_name} in {self.release_catalog_yaml_path}')
        elif len(current_release_bundle_schema) > 1:
            raise Exception(f'Multiple olm.bundle schema found for {self.current_bundle_name} in {self.release_catalog_yaml_path}')


    def parse_patch_yaml(self):
        return yaml.safe_load(open(self.patch_yaml_path))
    def patch_catalog_yaml(self):
        if 'olm.package' in self.patch_dict['patch']:
            self.patch_olm_package()
        if 'olm.channels' in self.patch_dict['patch']:
            self.patch_olm_channels()
        self.patch_olm_bundles()

        self.write_output_catalog()

    def write_output_catalog(self):
        docs = [doc for schema, schema_val in self.catalog_dict.items() for name, doc in schema_val.items()]
        # yaml.add_representer(str, str_presenter)
        # yaml.representer.SafeRepresenter.add_representer(str, str_presenter)
        # yaml.safe_dump_all(docs, open(self.output_file_path, 'w'), sort_keys=False)
        ruyaml.dump_all(docs, open(self.output_file_path, 'w'), Dumper=ruyaml.RoundTripDumper,
                    default_flow_style=False)


    def patch_olm_package(self):
        SCHEMA = 'olm.package'
        patch = self.patch_dict['patch'][SCHEMA]
        self.catalog_dict[SCHEMA][patch['name']] = jsonupdate_ng.updateJson(self.catalog_dict[SCHEMA][patch['name']], patch)


    def patch_olm_channels(self):
        SCHEMA = 'olm.channel'
        PATCH_SCHEMA = 'olm.channels'
        for channel in self.patch_dict['patch'][PATCH_SCHEMA]:
            if channel['name'] in self.catalog_dict[SCHEMA]:
                self.catalog_dict[SCHEMA][channel['name']] = jsonupdate_ng.updateJson(self.catalog_dict[SCHEMA][channel['name']], channel, meta={'listPatchScheme': {'$.entries': {'key': 'name'}}})
            else:
                self.catalog_dict[SCHEMA][channel['name']] = channel


    def patch_olm_bundles(self):
        self.patch_current_release_bundle_schema()

class snapshot_processor:
    GIT_URL_LABEL_KEY = 'git.url'
    GIT_COMMIT_LABEL_KEY = 'git.commit'
    FBC_FRAGMENT_REPO = 'rhoai-fbc-fragment'
    QUAY_BASE_URI = 'quay.io/rhoai'
    def __init__(self, rhoai_version:str, build_config_path:str, timeout:str, output_file_path:str, git_commit:str):
        self.output_file_path = output_file_path
        self.rhoai_version = rhoai_version
        self.build_config_path = build_config_path
        self.build_config = yaml.safe_load(open(self.build_config_path))
        self.ocp_versions_for_release = self.build_config['config']['supported-ocp-versions']['release']
        self.timeout = int(timeout * 60)
        self.git_commit = git_commit

    def monitor_fbc_builds(self):
        fbc_images = {}
        qc = quay_controller('rhoai')
        time_lapsed = 0
        def all_fbc_builds_finished():
            all_versions_covered = True
            for ocp_version in self.ocp_versions_for_release:
                if ocp_version not in fbc_images:
                    all_versions_covered = False
                    break
            return all_versions_covered

        while not all_fbc_builds_finished() and time_lapsed < self.timeout:
            for ocp_version in self.ocp_versions_for_release:
                fbc_image_tag = f'ocp-{ocp_version.strip("v")}-{self.rhoai_version}-{self.git_commit}'
                print(f'getting images for tag - {fbc_image_tag}')
                tags = qc.get_all_tags(self.FBC_FRAGMENT_REPO, fbc_image_tag)
                if not tags:
                    print(f'no tags found for {fbc_image_tag}, waiting..')
                for tag in tags:
                    sig_tag = f'{tag["manifest_digest"].replace(":", "-")}.sig'
                    signature = qc.get_tag_details(self.FBC_FRAGMENT_REPO, sig_tag)
                    if signature:
                        fbc_images[ocp_version] = f'{self.QUAY_BASE_URI}/{self.FBC_FRAGMENT_REPO}@{tag["manifest_digest"]}'
            time.sleep(30)
            time_lapsed += 45

        missing_images = []
        for ocp_version in self.ocp_versions_for_release:
            if ocp_version not in fbc_images:
                fbc_images[ocp_version] = 'NOT_FOUND'
                missing_images.append(ocp_version)

        json.dump(fbc_images, open(self.output_file_path, 'w'))
        if missing_images:
            print('FBC images not found for following OCP versions - ', missing_images)
            sys.exit(1)
        else:
            slack_message = f':staging: Successfully pushed to stage for {self.rhoai_version}!'
            for ocp_version, fbc_image in fbc_images.items():
                slack_message += f'\nFBCF image {ocp_version}: {fbc_image}'
            open('utils/slack_message.txt', 'w').write(slack_message)







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

def str_presenter(dumper, data):
    if data.count('\n') > 0:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "stage-catalog-patch" and "monitor-fbc-builds"', dest='operation')
    parser.add_argument('-c', '--catalog-yaml-path', required=False,
                        help='Path of the catalog.yaml from the main branch.', dest='catalog_yaml_path')
    parser.add_argument('-p', '--patch-yaml-path', required=False,
                        help='Path of the catalog-patch.yaml from the release branch.', dest='patch_yaml_path')
    parser.add_argument('-r', '--release-catalog-yaml-path', required=False,
                        help='Path of the catalog.yaml from the release branch', dest='release_catalog_yaml_path')
    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the output catalog yaml', dest='output_file_path')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    parser.add_argument('-t', '--timeout', required=False,
                        help='Timeout while waiting for FBC builds to finish', dest='timeout')
    parser.add_argument('-b', '--build-config-path', required=False,
                        help='Path of the build-config.yaml', dest='build_config_path')
    parser.add_argument('-g', '--git-commit', required=False,
                        help='expected git.commit of the FBC images', dest='git_commit')
    args = parser.parse_args()

    if args.operation.lower() == 'stage-catalog-patch':
        promoter = stage_promoter(catalog_yaml_path=args.catalog_yaml_path, patch_yaml_path=args.patch_yaml_path, release_catalog_yaml_path=args.release_catalog_yaml_path, output_file_path=args.output_file_path, rhoai_version=args.rhoai_version)
        promoter.patch_catalog_yaml()
    elif args.operation.lower() == 'monitor-fbc-builds':
        processor = snapshot_processor(rhoai_version=args.rhoai_version, build_config_path=args.build_config_path, timeout=args.timeout, output_file_path=args.output_file_path, git_commit=args.git_commit)
        processor.monitor_fbc_builds()

    # c = '/home/dchouras/RHODS/DevOps/FBC/main/catalog/v4.13/rhods-operator/catalog.yaml'
    # p = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/catalog/catalog-patch.yaml'
    # r = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/catalog/v4.13/rhods-operator/catalog.yaml'
    # o = 'output.yaml'
    # v = 'v2.13.0'
    # promoter = stage_promoter(catalog_yaml_path=c, patch_yaml_path=p, release_catalog_yaml_path=r, output_file_path=o, rhoai_version=v)
    # promoter.patch_catalog_yaml()

    # o = 'output.json'
    # v = 'v2.13.0'
    # b = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/config/build-config.yaml'
    # t = 120
    # g = 'd38006a8c055e7695a75364dbbfaf7c822fbd83c'
    #
    # processor = snapshot_processor(rhoai_version=v, build_config_path=b, timeout=t, output_file_path=o, git_commit=g)
    # processor.monitor_fbc_builds()
