"""Tests for generate-table.py JSON/YAML output and structured cell values."""

import importlib.util
import json
from pathlib import Path
import yaml
import pytest

# The script file has a hyphen in its name; load via importlib.util
_script_path = Path(__file__).parent / 'generate-table.py'
_spec = importlib.util.spec_from_file_location('generate_table', _script_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
get_structured_cell_value = _mod.get_structured_cell_value
get_cell_value = _mod.get_cell_value
generate_table = _mod.generate_table
normalize_architecture = _mod.normalize_architecture
extract_component_name = _mod.extract_component_name


SAMPLE_CONFIG = {
    'accelerator_incompatibility_rules': {
        'rocm': ['arm64', 'ppc64le', 's390x'],
        'cuda': ['ppc64le', 's390x'],
        'gaudi': ['arm64', 'ppc64le', 's390x'],
        'cpu': [],
    },
    'exception': [
        {
            'component': 'odh-mlmd-grpc-server-rhel9',
            'architectures': ['s390x'],
            'reason': 'Not yet implemented',
            'issue': 'https://issues.redhat.com/browse/RHOAIENG-38728',
        },
        {
            'component': 'odh-no-issue-rhel9',
            'architectures': ['ppc64le'],
            'reason': 'Missing vendor lib',
        },
    ],
}


# --- get_structured_cell_value tests ---

class TestGetStructuredCellValue:
    def test_supported(self):
        result = get_structured_cell_value('comp', 'amd64', {'amd64', 'arm64'}, SAMPLE_CONFIG)
        assert result == {'status': 'supported'}

    def test_exception_with_issue(self):
        result = get_structured_cell_value('odh-mlmd-grpc-server-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG)
        assert result['status'] == 'exception'
        assert result['issueKey'] == 'RHOAIENG-38728'
        assert result['issueUrl'] == 'https://issues.redhat.com/browse/RHOAIENG-38728'
        assert result['reason'] == 'Not yet implemented'

    def test_exception_without_issue(self):
        result = get_structured_cell_value('odh-no-issue-rhel9', 'ppc64le', {'amd64'}, SAMPLE_CONFIG)
        assert result['status'] == 'exception'
        assert result['issueKey'] == 'XXX'
        assert 'issueUrl' not in result

    def test_incompatible(self):
        result = get_structured_cell_value('odh-workbench-cuda-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG)
        assert result == {'status': 'incompatible', 'accelerator': 'cuda'}

    def test_not_built(self):
        result = get_structured_cell_value('odh-dashboard-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG)
        assert result == {'status': 'not_built'}

    def test_supported_takes_priority_over_exception(self):
        result = get_structured_cell_value('odh-mlmd-grpc-server-rhel9', 's390x', {'amd64', 's390x'}, SAMPLE_CONFIG)
        assert result['status'] == 'supported'


# --- get_cell_value backward compatibility ---

class TestGetCellValueBackwardCompat:
    def test_supported_returns_y(self):
        assert get_cell_value('comp', 'amd64', {'amd64'}, SAMPLE_CONFIG, 'markdown') == 'Y'

    def test_incompatible_returns_na(self):
        assert get_cell_value('odh-cuda-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG, 'text') == 'N/A'

    def test_not_built_returns_empty(self):
        assert get_cell_value('odh-dashboard-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG, 'text') == ''

    def test_exception_markdown_link(self):
        result = get_cell_value('odh-mlmd-grpc-server-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG, 'markdown')
        assert '[RHOAIENG-38728](' in result

    def test_exception_jira_link(self):
        result = get_cell_value('odh-mlmd-grpc-server-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG, 'jira')
        assert '[RHOAIENG-38728|' in result

    def test_exception_csv_hyperlink(self):
        result = get_cell_value('odh-mlmd-grpc-server-rhel9', 's390x', {'amd64'}, SAMPLE_CONFIG, 'csv')
        assert '=HYPERLINK(' in result

    def test_exception_no_issue_returns_xxx(self):
        result = get_cell_value('odh-no-issue-rhel9', 'ppc64le', {'amd64'}, SAMPLE_CONFIG, 'text')
        assert result == 'XXX'


# --- generate_table JSON/YAML tests ---

SAMPLE_COMPONENTS = {
    'odh-dashboard-rhel9': {'amd64', 'arm64', 'ppc64le', 's390x'},
    'odh-cuda-workbench-rhel9': {'amd64', 'arm64'},
    'odh-mlmd-grpc-server-rhel9': {'amd64', 'arm64', 'ppc64le'},
}


class TestGenerateTableJson:
    def test_valid_json(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        assert 'generatedAt' in data
        assert 'architectures' in data
        assert 'components' in data
        assert 'summary' in data

    def test_architectures_list(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        assert data['architectures'] == ['amd64', 'arm64', 'ppc64le', 's390x']

    def test_components_sorted(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        names = [c['name'] for c in data['components']]
        assert names == sorted(names)

    def test_component_has_all_archs(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        for comp in data['components']:
            assert set(comp['architectures'].keys()) == {'amd64', 'arm64', 'ppc64le', 's390x'}

    def test_summary_total_matches_components(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        assert data['summary']['totalComponents'] == len(data['components'])

    def test_summary_full_multi_arch(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        assert data['summary']['fullMultiArch'] == 1  # only odh-dashboard

    def test_branch_metadata(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json', metadata={'branch': 'rhoai-3.5'})
        data = json.loads(output)
        assert data['branch'] == 'rhoai-3.5'

    def test_no_branch_without_metadata(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json')
        data = json.loads(output)
        assert 'branch' not in data


class TestGenerateTableYaml:
    def test_valid_yaml(self):
        output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'yaml')
        data = yaml.safe_load(output)
        assert 'generatedAt' in data
        assert 'architectures' in data
        assert 'components' in data
        assert 'summary' in data

    def test_yaml_same_structure_as_json(self):
        json_output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'json', metadata={'branch': 'test'})
        yaml_output = generate_table(SAMPLE_COMPONENTS, SAMPLE_CONFIG, 'yaml', metadata={'branch': 'test'})
        json_data = json.loads(json_output)
        yaml_data = yaml.safe_load(yaml_output)
        # generatedAt will differ slightly, so compare everything else
        del json_data['generatedAt']
        del yaml_data['generatedAt']
        assert json_data == yaml_data


# --- Existing function tests (regression) ---

class TestNormalizeArchitecture:
    def test_linux_x86_64(self):
        assert normalize_architecture('linux/x86_64') == 'amd64'

    def test_linux_m2xlarge_arm64(self):
        assert normalize_architecture('linux-m2xlarge/arm64') == 'arm64'

    def test_linux_ppc64le(self):
        assert normalize_architecture('linux/ppc64le') == 'ppc64le'

    def test_bare_arch(self):
        assert normalize_architecture('s390x') == 's390x'


class TestExtractComponentName:
    def test_quay_prefix(self):
        assert extract_component_name('quay.io/rhoai/odh-dashboard-rhel9:{{target_branch}}') == 'odh-dashboard-rhel9'

    def test_no_prefix(self):
        assert extract_component_name('odh-dashboard-rhel9:latest') == 'odh-dashboard-rhel9'

    def test_no_tag(self):
        assert extract_component_name('quay.io/rhoai/odh-dashboard-rhel9') == 'odh-dashboard-rhel9'
