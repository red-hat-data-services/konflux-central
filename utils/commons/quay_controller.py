import json

import requests
import yaml
import os
import traceback
BASE_URL = 'https://quay.io/api/v1'

class quay_controller:
    def __init__(self, org:str):
        self.org = org


    def invoke_quay_controller(self):
        print('invoked qc')
    def create_repo(self, repo:str):
        url = f'{BASE_URL}/repository'
        try:
            print(f'Creating repo - {repo}')
            body = {
              "repository": repo,
              "visibility": "private",
              "namespace": self.org,
              "description": f"To host the intermedidate build images of {repo.split(self.suffix)[0]}",
              "repo_kind": "image"
            }
            print(body)
            headers = {'Authorization': f'Bearer {os.environ[self.org + "_token"]}', 'Content-Type': 'application/json', 'Accept': 'application/json'}
            response = requests.post(url, headers=headers, data=json.dumps(body))
            print(response.status_code)
            print(response.text)
            print(f'Created quay.io/{self.org}/{repo}')
        except Exception as e:
            print(e)
            print(traceback.format_exc())
