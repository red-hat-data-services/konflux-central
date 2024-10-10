import os, requests
from jsonupdate_ng import jsonupdate_ng
import argparse
import yaml
import ruamel.yaml as ruyaml
import json
from collections import defaultdict
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
import base64
class fbc_processor:
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    def __init__(self, build_config_path:str, catalog_yaml_path:str, patch_yaml_path:str, single_bundle_path:str, output_file_path:str, push_pipeline_operation:str, push_pipeline_yaml_path:str):
        self.build_config_path = build_config_path
        self.catalog_yaml_path = catalog_yaml_path
        self.patch_yaml_path = patch_yaml_path
        self.single_bundle_path = single_bundle_path
        self.output_file_path = output_file_path
        self.catalog_dict:defaultdict = self.parse_catalog_yaml()
        self.patch_dict = self.parse_patch_yaml()
        self.build_config = yaml.safe_load(open(self.build_config_path))
        self.current_olm_bundle = self.parse_single_bundle_catalog()
        self.push_pipeline_operation = push_pipeline_operation
        self.push_pipeline_yaml_path = push_pipeline_yaml_path
        self.push_pipeline_dict = ruyaml.load(open(self.push_pipeline_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)

    def parse_catalog_yaml(self):
        # objs = yaml.safe_load_all(open(self.catalog_yaml_path))
        objs = ruyaml.load_all(open(self.catalog_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        print(type(objs))
        catalog_dict = defaultdict(dict)
        for obj in objs:
            catalog_dict[obj['schema']][obj['name']] = obj
        return catalog_dict

    def parse_single_bundle_catalog(self):
        objs = yaml.safe_load_all(open(self.single_bundle_path))
        # objs = ruyaml.load_all(open(self.catalog_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        single_olm_bundle = None
        for obj in objs:
            if obj['schema'] == 'olm.bundle':
                single_olm_bundle = obj
                break
        return single_olm_bundle

    def parse_patch_yaml(self):
        return yaml.safe_load(open(self.patch_yaml_path))
    def patch_catalog_yaml(self):
        if 'olm.package' in self.patch_dict['patch']:
            self.patch_olm_package()
        if 'olm.channels' in self.patch_dict['patch']:
            self.patch_olm_channels()
        self.patch_olm_bundles()

        self.process_push_pipeline()

        self.write_output_catalog()

    def write_output_catalog(self):
        docs = [doc for schema, schema_val in self.catalog_dict.items() for name, doc in schema_val.items()]
        # yaml.add_representer(str, str_presenter)
        # yaml.representer.SafeRepresenter.add_representer(str, str_presenter)
        # yaml.safe_dump_all(docs, open(self.output_file_path, 'w'), sort_keys=False)

        ruyaml.dump_all(docs, open(self.output_file_path, 'w'), Dumper=ruyaml.RoundTripDumper,
                        default_flow_style=False)

    def process_push_pipeline(self):
        current_on_cel_expr = self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression']
        disable_ext = 'non-existent-file.non-existent-ext'
        disable_expr = f'&& "{disable_ext}".pathChanged()'
        updated=False
        if self.push_pipeline_operation.lower() == 'enable' and disable_ext in current_on_cel_expr:
            self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression'] = current_on_cel_expr.replace(disable_expr, '')
            updated = True
        elif self.push_pipeline_operation.lower() == 'disable' and disable_ext not in current_on_cel_expr:
            self.push_pipeline_dict['metadata']['annotations']['pipelinesascode.tekton.dev/on-cel-expression'] = f'{current_on_cel_expr} {disable_expr}'
            updated = True

        if updated:
            ruyaml.dump(self.push_pipeline_dict, open(self.push_pipeline_yaml_path, 'w'), Dumper=ruyaml.RoundTripDumper,
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

    def apply_replacements_to_catalog(self, olm_bundle):
        olm_bundle['image'] = self.apply_replacement(olm_bundle['image'])

        for relatedImage in olm_bundle['relatedImages']:
            relatedImage['image'] = self.apply_replacement(relatedImage['image'])

        # Commenting this out since bundle is now referncing to the RH registry images
        # for property in olm_bundle['properties']:
        #     if property['type'] == 'olm.bundle.object':
        #         property['value']['data'] = self.apply_replacemenmt_to_olm_bundle_object(property['value']['data'])
        return olm_bundle


    def apply_replacemenmt_to_olm_bundle_object(self, encoded_object:str):
        bundle_str:str = base64.b64decode(encoded_object).decode('utf-8')
        bundle_object = json.loads(bundle_str)
        encoded_output = encoded_object
        if bundle_object['kind'] == 'ClusterServiceVersion':
            envs = \
            bundle_object['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'env']
            for env in envs:
                if 'value' in env:
                    env['value'] = self.apply_replacement(env['value'])
            encoded_output = base64.b64encode(json.dumps(bundle_object).encode()).decode('utf-8')
            encoded_output = encoded_output.replace('\n', '')

        return encoded_output



    def apply_replacement(self, value:str):
        if value:
            for replacement in self.build_config['config']['replacements']:
                intermediate_registry = replacement['registry']
                for old, new in replacement['repo_mappings'].items():
                    value = value.replace(f'{intermediate_registry}/{old}@', f'{self.PRODUCTION_REGISTRY}/{new}@')
        return value


    def patch_olm_bundles(self):
        SCHEMA = 'olm.bundle'
        current_bundle_name = self.current_olm_bundle['name']
        self.catalog_dict[SCHEMA][current_bundle_name] = self.apply_replacements_to_catalog(self.current_olm_bundle)
        # apply replacements for bundle image Uri in catalog
        # apply replacements for related images in the catalog
        # replacement of the encoded bundle
        # 1. decode the last "olm.bundle" object
        # 2. apply the replacements for all the related images
        # 3. encode the "olm.bundle" again
        # 4. patch it with the catalog.yaml
def str_presenter(dumper, data):
    if data.count('\n') > 0:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)
class snapshot_processor:
    def __init__(self, snapshot_json_path:str, output_file_path:str, rhoai_version:str, build_config_path:str, image_filter:str=''):
        self.snapshot_json_path = snapshot_json_path
        self.output_file_path = output_file_path
        self.image_filter = image_filter
        self.rhoai_version = rhoai_version
        self.build_config_path = build_config_path
        self.build_config = yaml.safe_load(open(self.build_config_path))

    def extract_images_from_snapshot(self):
        snapshot = json.load(open(self.snapshot_json_path))
        output_images = []
        for component in snapshot['spec']['components']:
            output_images.append({'name': component['name'], 'imageUri': component['containerImage']})

        json.dump(output_images, indent=4, fp=open(self.output_file_path, 'w'))

    def get_all_latest_images(self):
        latest_images = []
        qc = quay_controller('rhoai')
        for registry_entry in self.build_config['config']['replacements']:
            registry = registry_entry['registry']
            for repo_path in [repo_path for repo_path in registry_entry['repo_mappings'] if self.image_filter in repo_path]:
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

        json.dump(latest_images, indent=4, fp=open(self.output_file_path, 'w'))


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
                        help='Operation code, supported values are "catalog-patch", "extract-snapshot-images" and "push-pipeline-update"', dest='operation')
    parser.add_argument('-b', '--build-config-path', required=False,
                        help='Path of the build-config.yaml', dest='build_config_path')
    parser.add_argument('-c', '--catalog-yaml-path', required=False,
                        help='Path of the catalog.yaml from the main branch.', dest='catalog_yaml_path')
    parser.add_argument('-p', '--patch-yaml-path', required=False,
                        help='Path of the catalog-patch.yaml from the release branch.', dest='patch_yaml_path')
    parser.add_argument('-s', '--single-bundle-path', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='single_bundle_path')
    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the output catalog yaml', dest='output_file_path')
    parser.add_argument('-sn', '--snapshot-json-path', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='snapshot_json_path')
    parser.add_argument('-f', '--image-filter', required=False,
                        help='Path of the single-bundle generated using the opm.', dest='image_filter')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    parser.add_argument('-y', '--push-pipeline-yaml-path', required=False,
                        help='Path of the tekton pipeline for push builds', dest='push_pipeline_yaml_path')
    parser.add_argument('-x', '--push-pipeline-operation', required=False, default="enable",
                        help='Operation code, supported values are "enable" and "disable"', dest='push_pipeline_operation')

    args = parser.parse_args()

    if args.operation.lower() == 'catalog-patch':
        processor = fbc_processor(build_config_path=args.build_config_path, catalog_yaml_path=args.catalog_yaml_path, patch_yaml_path=args.patch_yaml_path, single_bundle_path=args.single_bundle_path, output_file_path=args.output_file_path, push_pipeline_operation=args.push_pipeline_operation, push_pipeline_yaml_path=args.push_pipeline_yaml_path)
        processor.patch_catalog_yaml()
    elif args.operation.lower() == 'extract-snapshot-images':
        processor = snapshot_processor(snapshot_json_path=args.snapshot_json_path, output_file_path=args.output_file_path, image_filter=args.image_filter, rhoai_version=args.rhoai_version, build_config_path=args.build_config_path)
        processor.get_all_latest_images()

        # c = '/home/dchouras/RHODS/DevOps/FBC/main/catalog/v4.13/rhods-operator/catalog.yaml'
        # p = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/catalog/catalog-patch.yaml'
        # s = '/home/dchouras/RHODS/DevOps/FBC/fbc-utils/utils/single_bundle_catalog_semver.yaml'
        # o = 'output.yaml'
        # b = '/home/dchouras/RHODS/DevOps/FBC/fbc-utils/utils/build-config.yaml'
        # push_pipeline_operation = 'enable'
        # push_pipeline_yaml_path = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/.tekton/odh-operator-bundle-v2-13-push.yaml'
        # processor = fbc_processor(build_config_path=b, catalog_yaml_path=c, patch_yaml_path=p, single_bundle_path=s, output_file_path=o,
        #                               push_pipeline_yaml_path=push_pipeline_yaml_path, push_pipeline_operation=push_pipeline_operation)
        # processor.patch_catalog_yaml()