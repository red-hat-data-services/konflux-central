

class catalog_validator:
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-op', '--operation', required=False,
                        help='Operation code, supported values are "validate-release-catalog" and "validate-stage-catalog"', dest='operation')
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
    args = parser.parse_args()

    if args.operation.lower() == 'catalog-patch':
        processor = fbc_processor(build_config_path=args.build_config_path, catalog_yaml_path=args.catalog_yaml_path, patch_yaml_path=args.patch_yaml_path, single_bundle_path=args.single_bundle_path, output_file_path=args.output_file_path)
        processor.patch_catalog_yaml()
    elif args.operation.lower() == 'extract-snapshot-images':
        processor = snapshot_processor(snapshot_json_path=args.snapshot_json_path, output_file_path=args.output_file_path, image_filter=args.image_filter, rhoai_version=args.rhoai_version, build_config_path=args.build_config_path)
        processor.get_all_latest_images()