import sys

import yaml
from collections import defaultdict
import itertools
class catalog_validator:
    MISSING_BUNDLE_EXCEPTIONS = ['rhods-operator.2.9.0', 'rhods-operator.2.9.1'] #ref - RHOAIENG-8828
    def __init__(self, build_config_path, catalog_folder_path, shipped_rhoai_versions_path):
        self.build_config_path = build_config_path
        self.catalog_folder_path = catalog_folder_path
        self.shipped_rhoai_versions_path = shipped_rhoai_versions_path
        self.pcc_catalog_files = ['bundle_object_catalog.yaml', 'csv_meta_catalog.yaml']

        self.build_config = yaml.safe_load(open(self.build_config_path))
        self.supported_ocp_versions = sorted(list(set(self.build_config['config']['supported-ocp-versions']['release'] + [item['name'] for item in self.build_config['config']['supported-ocp-versions']['build']])))
        self.shipped_rhoai_versions = open(self.shipped_rhoai_versions_path).readlines()

        self.shipped_rhoai_versions = sorted(list(
            set([version.split('-')[0].strip('\n').replace('v', '') for version in self.shipped_rhoai_versions if
                 version.count('.') > 1])))
        print('shipped_rhoai_versions', self.shipped_rhoai_versions)

    def parse_catalog_yaml(self, catalog_yaml_path):
        # objs = yaml.safe_load_all(open(self.catalog_yaml_path))
        objs = yaml.safe_load_all(open(catalog_yaml_path))
        catalog_dict = defaultdict(dict)
        for obj in objs:
            catalog_dict[obj['schema']][obj['name']] = obj
        return catalog_dict

    def validate_catalogs(self):
        missing_bundles = {}
        for ocp_version in self.supported_ocp_versions:
            catalog_dict = self.parse_catalog_yaml(f'{self.catalog_folder_path}/{ocp_version}/rhods-operator/catalog.yaml')
            bundles = catalog_dict['olm.bundle']
            missing_bundles[ocp_version] = []
            for rhoai_version in self.shipped_rhoai_versions:
                operator_name = f'rhods-operator.{rhoai_version}'
                if operator_name not in bundles and operator_name not in self.MISSING_BUNDLE_EXCEPTIONS:
                    missing_bundles[ocp_version].append(operator_name)

        print('missing_bundles', missing_bundles)

        if list(itertools.chain.from_iterable([bundles for ocp_version, bundles in missing_bundles.items()])):
            print('Following bundles are missing from the catalogs:', missing_bundles)
            print('Exiting, please fix the missing bundles')
            sys.exit(1)
        else:
            print('No missing bundles found in all the catalogs')

    def validate_pcc(self):
        missing_bundles = {}

        for pcc_file in self.pcc_catalog_files:
            catalog_dict = self.parse_catalog_yaml(f'{self.catalog_folder_path}/{pcc_file}')
            bundles = catalog_dict['olm.bundle']
            missing_bundles[pcc_file] = []
            for rhoai_version in self.shipped_rhoai_versions:
                operator_name = f'rhods-operator.{rhoai_version}'
                if operator_name not in bundles and operator_name not in self.MISSING_BUNDLE_EXCEPTIONS:
                    missing_bundles[pcc_file].append(operator_name)

        print('missing_bundles', missing_bundles)

        if list(itertools.chain.from_iterable([bundles for ocp_version, bundles in missing_bundles.items()])):
            print('Following bundles are missing from the catalogs:', missing_bundles)
            print('Exiting, please fix the missing bundles')
            sys.exit(1)
        else:
            print('No missing bundles found in all the catalogs')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "validate-catalogs" and "validate-pcc"', dest='operation')
    parser.add_argument('-b', '--build-config-path', required=False,
                        help='Path of the build-config.yaml', dest='build_config_path')
    parser.add_argument('-c', '--catalog-folder-path', required=False,
                        help='Path of the catalog.yaml from the main branch.', dest='catalog_folder_path')
    parser.add_argument('-s', '--shipped-rhoai-versions-path', required=False,
                        help='Path of the shipped_rhoai_versions.txt from the main branch.', dest='shipped_rhoai_versions_path')
    args = parser.parse_args()

    if args.operation.lower() == 'validate-catalogs':
        validator = catalog_validator(build_config_path=args.build_config_path, catalog_folder_path=args.catalog_folder_path, shipped_rhoai_versions_path=args.shipped_rhoai_versions_path)
        validator.validate_catalogs()
    elif args.operation.lower() == 'validate-pcc':
        validator = catalog_validator(build_config_path=args.build_config_path, catalog_folder_path=args.catalog_folder_path, shipped_rhoai_versions_path=args.shipped_rhoai_versions_path)
        validator.validate_pcc()


    # build_config_path = '/home/dchouras/RHODS/DevOps/RBC/rhoai-2.17/config/build-config.yaml'
    # shipped_rhoai_versions_path = '/home/dchouras/RHODS/DevOps/RBC/main/pcc/shipped_rhoai_versions.txt'
    #
    # catalog_folder_path = '/home/dchouras/RHODS/DevOps/RBC-RHDS/catalog'
    # stage_catalog_folder_path = '/home/dchouras/RHODS/DevOps/RBC/main/catalog/rhoai-2.17'
    # pcc_folder_path = '/home/dchouras/RHODS/DevOps/RBC/main/pcc'


    # validator = catalog_validator(build_config_path=build_config_path, catalog_folder_path=catalog_folder_path,
    #                               shipped_rhoai_versions_path=shipped_rhoai_versions_path)
    # validator.validate_catalogs()

    # validator = catalog_validator(build_config_path=build_config_path, catalog_folder_path=stage_catalog_folder_path,
    #                               shipped_rhoai_versions_path=shipped_rhoai_versions_path)
    # validator.validate_catalogs()

    # validator = catalog_validator(build_config_path=build_config_path, catalog_folder_path=pcc_folder_path,
    #                               shipped_rhoai_versions_path=shipped_rhoai_versions_path)
    # validator.validate_pcc()
