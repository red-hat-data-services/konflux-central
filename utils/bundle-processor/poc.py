bundle_csv_path = '/home/dchouras/RHODS/DevOps/FBC/rhoai-2.13/bundle/manifests/rhods-operator.clusterserviceversion.yml'
output_file_path = 'output.yaml'
import ruamel.yaml as yaml

csv = yaml.load(open(bundle_csv_path), Loader=yaml.RoundTripLoader, preserve_quotes=True)
yaml.dump(csv, open(output_file_path, 'w'), Dumper=yaml.RoundTripDumper, default_flow_style=False)