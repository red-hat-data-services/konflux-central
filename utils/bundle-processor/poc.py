bundle_csv_path = '/home/dchouras/RHODS/DevOps/FBC/rhoai-2.13/bundle/manifests/rhods-operator.clusterserviceversion.yml'
output_file_path = 'output.yaml'
import ruamel.yaml as yaml

csv = yaml.load(open(bundle_csv_path), Loader=yaml.RoundTripLoader, preserve_quotes=True)
env_list = csv['spec']['install']['spec']['deployments'][0]['spec']['template']['spec']['containers'][0][
                'env']
env_list = [dict(item) for item in env_list]
print(env_list)
yaml.dump(csv, open(output_file_path, 'w'), Dumper=yaml.RoundTripDumper, default_flow_style=False)