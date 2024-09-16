import json
import os
import traceback
import yaml
from quay_controller import quay_controller
BASE_URL = 'https://quay.io/api/v1'

class quay_onboarder:
    def __init__(self, org:str, repo_file_path:str):
        self.suffix = '-rhel8'
        self.org = org
        self.repos = yaml.safe_load(open(repo_file_path))['repos']
        self.repos = [f'{repo}{self.suffix}' for repo in self.repos if not repo.endswith(self.suffix)]
        self.qc = quay_controller(org=self.org)

    def create_repos(self):
        for repo in self.repos:
            # self.qc.create_repo(repo)
            print(f'Created repo {repo}')

if __name__ == '__main__':
    qo = quay_onboarder(org='rhoai', repo_file_path='repos.yaml')
    qo.create_repos()
