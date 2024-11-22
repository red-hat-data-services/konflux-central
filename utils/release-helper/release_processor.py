import argparse
import json
import yaml
import ruamel.yaml as ruyaml
from collections import defaultdict
class release_processor:
    OPERATOR_NAME = 'rhods-operator'
    PRODUCTION_REGISTRY = 'registry.redhat.io'
    DEV_REGISTRY = 'quay.io'
    RHOAI_NAMESPACE = 'rhoai'
    def __init__(self, catalog_yaml_path:str, rhoai_version:str, output_file_path:str):
        self.catalog_yaml_path = catalog_yaml_path
        self.catalog_dict:defaultdict = self.parse_catalog_yaml()
        self.rhoai_version = rhoai_version
        self.output_file_path = output_file_path
        self.current_operator = f'{self.OPERATOR_NAME}.{self.rhoai_version}'

    def parse_catalog_yaml(self):
        # objs = yaml.safe_load_all(open(self.catalog_yaml_path))
        objs = ruyaml.load_all(open(self.catalog_yaml_path), Loader=ruyaml.RoundTripLoader, preserve_quotes=True)
        catalog_dict = defaultdict(dict)
        for obj in objs:
            catalog_dict[obj['schema']][obj['name']] = obj
        return catalog_dict
    def extract_rhoai_images_from_catalog(self):
        expected_rhoai_images = [image['image'] for image in self.catalog_dict['olm.bundle'][self.current_operator]['relatedImages'] if f'{self.PRODUCTION_REGISTRY}/{self.RHOAI_NAMESPACE}/' in image['image']]
        expected_rhoai_images = [image.replace(f'{self.PRODUCTION_REGISTRY}/{self.RHOAI_NAMESPACE}/', f'{self.DEV_REGISTRY}/{self.RHOAI_NAMESPACE}/') for image in expected_rhoai_images]
        json.dump(expected_rhoai_images, open(self.output_file_path, 'w'), indent=4)

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


        # for expected_image in self.expected_rhoai_images:

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "extract-rhoai-images-from-catalog" and "check-snapshot-compatibility"',
                        dest='operation')
    parser.add_argument('-c', '--catalog-yaml-path', required=False,
                        help='Path of the catalog.yaml from the current catalog.', dest='catalog_yaml_path')
    parser.add_argument('-v', '--rhoai-version', required=False,
                        help='The version of Openshift-AI being processed', dest='rhoai_version')
    parser.add_argument('-o', '--output-file-path', required=False,
                        help='Path of the output images yaml', dest='output_file_path')
    parser.add_argument('-s', '--snapshot-file-path', required=False,
                        help='Path of the snapshot yaml', dest='snapshot_file_path')
    parser.add_argument('-n', '--snapshot-name', required=False,
                        help='Path of the snapshot yaml', dest='snapshot_name')
    parser.add_argument('-e', '--expected-rhoai-images-file-path', required=False,
                        help='expected rhoai images in the catalog yaml', dest='expected_rhoai_images_file_path')

    args = parser.parse_args()

    if args.operation.lower() == 'extract-rhoai-images-from-catalog':
        processor = release_processor(catalog_yaml_path=args.catalog_yaml_path, rhoai_version=args.rhoai_version, output_file_path=args.output_file_path)
        processor.extract_rhoai_images_from_catalog()
    elif args.operation.lower() == 'check-snapshot-compatibility':
        processor = snapshot_processor(snapshot_file_path=args.snapshot_file_path, expected_rhoai_images_file_path=args.expected_rhoai_images_file_path, snapshot_name=args.snapshot_name)
        processor.check_snapshot_compatibility()
