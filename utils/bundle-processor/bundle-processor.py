
from jsonupdate_ng import jsonupdate_ng
import argparse
import yaml
import json
class bundle_processor:
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    def __init__(self, build_config_path:str, bundle_csv_path:str, patch_yaml_path:str, snapshot_json_path:str, output_file_path:str):
        self.build_config_path = build_config_path
        self.bundle_csv_path = bundle_csv_path
        self.patch_yaml_path = patch_yaml_path
        self.snapshot_json_path = snapshot_json_path
        self.output_file_path = output_file_path
        self.csv_dict = self.parse_csv_yaml()
        self.patch_dict = self.parse_patch_yaml()
        self.build_config = yaml.safe_load(open(self.build_config_path))

    def parse_csv_yaml(self):
        csv_dict = yaml.safe_load(open(self.bundle_csv_path))
        return csv_dict


    def parse_patch_yaml(self):
        return yaml.safe_load(open(self.patch_yaml_path))
    def patch_bundle_csv(self):
        processor = snapshot_processor(snapshot_json_path=self.snapshot_json_path, output_file_path=None)
        self.latest_images = processor.extract_images_from_snapshot()

        if self.latest_images:
            self.patch_related_images()

        self.write_output_catalog()

    def write_output_catalog(self):
        docs = [doc for schema, schema_val in self.catalog_dict.items() for name, doc in schema_val.items()]
        yaml.add_representer(str, str_presenter)
        yaml.representer.SafeRepresenter.add_representer(str, str_presenter)
        yaml.safe_dump_all(docs, open(self.output_file_path, 'w'))

    def patch_related_images(self):
        SCHEMA = 'relatedImages'
        PATCH_SCHEMA = 'olm.channels'
        env_list = self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'env']
        env_object = jsonupdate_ng.updateJson({'env': env_list}, {'env': self.latest_images}, meta={'listPatchScheme': {'$.env': 'name'}})
        self.csv_dict['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
            'env'] = env_object['env']

    def apply_replacements_to_related_images(self):
        for relatedImage in self.patch_dict['patch']['relatedImages']:
            relatedImage['value'] = self.apply_replacement(relatedImage['value'])

    def apply_replacement(self, value:str):
        if value:
            for replacement in self.build_config['config']['replacements']:
                intermediate_registry = replacement['registry']
                for old, new in replacement['repo_mappings'].items():
                    value = value.replace(f'{intermediate_registry}/{old}@', f'{self.PRODUCTION_REGISTRY}/{new}@')
        return value

def str_presenter(dumper, data):
    if data.count('\n') > 0:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
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
            output_images.append({'name': component['name'], 'value': component['containerImage']})

        return output_images


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
    args = parser.parse_args()

    if args.operation.lower() == 'bundle-patch':
        processor = bundle_processor(build_config_path=args.build_config_path, bundle_csv_path=args.bundle_csv_path, patch_yaml_path=args.patch_yaml_path, snapshot_json_path=args.snapshot_json_path, output_file_path=args.output_file_path)
        processor.patch_bundle_csv()