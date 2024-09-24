import os, requests
from jsonupdate_ng import jsonupdate_ng
import argparse
import yaml
import ruamel.yaml as ruyaml
import json
from collections import defaultdict
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
import base64
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
            raise Exception(f'No olm.bundle schema found in the catalog.yaml for {self.current_bundle_name} ')
        elif len(current_release_bundle_schema) > 1:
            raise Exception(f'Multiple olm.bundle schema found in the catalog.yaml for {self.current_bundle_name} ')


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
                self.catalog_dict[SCHEMA][channel['name']] = jsonupdate_ng.updateJson(self.catalog_dict[SCHEMA][channel['name']], channel, meta={'listPatchScheme': {'$.entries': 'name'}})
            else:
                self.catalog_dict[SCHEMA][channel['name']] = channel


    def patch_olm_bundles(self):
        self.patch_current_release_bundle_schema()

def str_presenter(dumper, data):
    if data.count('\n') > 0:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)



if __name__ == '__main__':
    # parser = argparse.ArgumentParser()
    # parser.add_argument('-op', '--operation', required=False,
    #                     help='Operation code, supported values are "catalog-patch" and "extract-snapshot-images"', dest='operation')
    # parser.add_argument('-c', '--catalog-yaml-path', required=False,
    #                     help='Path of the catalog.yaml from the main branch.', dest='catalog_yaml_path')
    # parser.add_argument('-p', '--patch-yaml-path', required=False,
    #                     help='Path of the catalog-patch.yaml from the release branch.', dest='patch_yaml_path')
    # parser.add_argument('-r', '--release-catalog-yaml-path', required=False,
    #                     help='Path of the catalog.yaml from the release branch', dest='release_catalog_yaml_path')
    # parser.add_argument('-o', '--output-file-path', required=False,
    #                     help='Path of the output catalog yaml', dest='output_file_path')
    # parser.add_argument('-v', '--rhoai-version', required=False,
    #                     help='The version of Openshift-AI being processed', dest='rhoai_version')
    # args = parser.parse_args()
    #
    # if args.operation.lower() == 'stage-catalog-patch':
    #     promoter = stage_promoter(catalog_yaml_path=args.catalog_yaml_path, patch_yaml_path=args.patch_yaml_path, release_catalog_yaml_path=args.release_catalog_yaml_path, output_file_path=args.output_file_path, rhoai_version=args.rhoai_version)
    #     promoter.patch_catalog_yaml()

        c = '/home/dchouras/RHODS/DevOps/FBC/main/catalog/v4.13/rhods-operator/catalog.yaml'
        p = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/catalog/catalog-patch.yaml'
        r = '/home/dchouras/RHODS/DevOps/RHOAI-Build-Config/catalog/v4.13/rhods-operator/catalog.yaml'
        o = 'output.yaml'
        v = 'v2.13.0'
        promoter = stage_promoter(catalog_yaml_path=c, patch_yaml_path=p, release_catalog_yaml_path=r, output_file_path=o, rhoai_version=v)
        promoter.patch_catalog_yaml()