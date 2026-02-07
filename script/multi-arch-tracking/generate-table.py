#!/usr/bin/env python3
"""
Architecture Support Table Generator

This script scans PipelineRun YAML files in the konflux-central repository
and generates a table showing which architectures each component supports.
"""

import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Optional
import yaml


def normalize_architecture(platform: str) -> str:
    """
    Normalize platform string to architecture name.

    Strips prefixes like 'linux/', 'linux-extra-fast/', 'linux-m2xlarge/', etc.
    and normalizes 'x86_64' to 'amd64'.

    Examples:
        linux/x86_64 -> amd64
        linux-m2xlarge/arm64 -> arm64
        linux-extra-fast/amd64 -> amd64
    """
    # Extract architecture after the last '/'
    if '/' in platform:
        arch = platform.split('/')[-1]
    else:
        arch = platform

    # Normalize x86_64 to amd64
    if arch == 'x86_64':
        arch = 'amd64'

    return arch


def extract_component_name(output_image: str) -> str:
    """
    Extract component name from output-image value.

    Strips 'quay.io/rhoai/' prefix and ':{{target_branch}}' suffix.
    Keeps the full component name including -rhel9 suffix.

    Examples:
        quay.io/rhoai/odh-kserve-controller-rhel9:{{target_branch}} -> odh-kserve-controller-rhel9
        quay.io/rhoai/odh-rhel9-operator:{{target_branch}} -> odh-rhel9-operator
    """
    # Remove quay.io/rhoai/ prefix
    if output_image.startswith('quay.io/rhoai/'):
        name = output_image[len('quay.io/rhoai/'):]
    else:
        name = output_image

    # Remove :{{target_branch}} or similar suffix
    if ':' in name:
        name = name.split(':')[0]

    return name


def parse_pipelinerun_from_content(file_path: str, content: str) -> tuple[Optional[str], Set[str]]:
    """
    Parse PipelineRun YAML content and extract component name and architectures.

    Args:
        file_path: Path to the file (for error reporting only)
        content: YAML content as string

    Returns:
        Tuple of (component_name, set of architectures)
    """
    try:
        data = yaml.safe_load(content)

        if not data or 'spec' not in data or 'params' not in data['spec']:
            return None, set()

        output_image = None
        platforms = []

        # Extract parameters
        for param in data['spec']['params']:
            if param.get('name') == 'output-image':
                output_image = param.get('value')
            elif param.get('name') == 'build-platforms':
                platforms = param.get('value', [])

        if not output_image or not platforms:
            return None, set()

        # Extract component name
        component_name = extract_component_name(output_image)

        # Normalize architectures
        architectures = {normalize_architecture(p) for p in platforms}

        return component_name, architectures

    except Exception as e:
        print(f"Warning: Error parsing {file_path}: {e}", file=sys.stderr)
        return None, set()


def parse_pipelinerun(file_path: Path) -> tuple[Optional[str], Set[str]]:
    """
    Parse a PipelineRun YAML file and extract component name and architectures.

    Returns:
        Tuple of (component_name, set of architectures)
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return parse_pipelinerun_from_content(str(file_path), content)
    except Exception as e:
        print(f"Warning: Error reading {file_path}: {e}", file=sys.stderr)
        return None, set()


def find_pipelinerun_files(base_dir: Path) -> List[Path]:
    """
    Find all PipelineRun YAML files in pipelineruns/*/.tekton/ directories.
    """
    pattern = "pipelineruns/*/.tekton/*.yaml"
    files = list(base_dir.glob(pattern))
    return sorted(files)


def load_config(config_path: Optional[Path]) -> dict:
    """
    Load TOML configuration file with exceptions and accelerator rules.

    Returns:
        Dict with 'accelerator_incompatibility_rules' and 'exception' keys
    """
    if not config_path or not config_path.exists():
        return {
            'accelerator_incompatibility_rules': {},
            'exception': []
        }

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            print("Warning: toml library not available. Install with: pip install tomli", file=sys.stderr)
            return {
                'accelerator_incompatibility_rules': {},
                'exception': []
            }

    try:
        with open(config_path, 'rb') as f:
            config = tomllib.load(f)
        return config
    except Exception as e:
        print(f"Warning: Error loading config file {config_path}: {e}", file=sys.stderr)
        return {
            'accelerator_incompatibility_rules': {},
            'exception': []
        }


def validate_git_branch(repo_dir: Path, branch: str) -> bool:
    """
    Validate that a git branch or ref exists in the repository.

    Args:
        repo_dir: Path to the git repository
        branch: Branch name or git ref to validate

    Returns:
        True if the branch exists, False otherwise

    Raises:
        ValueError: If the directory is not a git repository or git is not available
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', branch],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except FileNotFoundError:
        raise ValueError("git command not found. Please ensure git is installed.")
    except Exception as e:
        raise ValueError(f"Error validating git branch: {e}")


def find_pipelinerun_files_from_git(repo_dir: Path, branch: str) -> List[str]:
    """
    Find all PipelineRun YAML files in a git branch.

    Uses git ls-tree to list files matching the pattern pipelineruns/*/.tekton/*.yaml
    without checking out the branch.

    Args:
        repo_dir: Path to the git repository
        branch: Branch name or git ref to read from

    Returns:
        Sorted list of file paths relative to repository root

    Raises:
        ValueError: If git command fails
    """
    try:
        # List all files in pipelineruns/ directory recursively
        result = subprocess.run(
            ['git', 'ls-tree', '-r', '--name-only', branch, 'pipelineruns/'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )

        # Filter for files matching pattern: pipelineruns/*/.tekton/*.yaml
        all_files = result.stdout.strip().split('\n')
        yaml_files = [
            f for f in all_files
            if f and '/.tekton/' in f and f.endswith('.yaml')
        ]

        return sorted(yaml_files)

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise ValueError(f"Error listing files from git branch '{branch}': {error_msg}")
    except Exception as e:
        raise ValueError(f"Unexpected error listing files from git: {e}")


def read_file_from_git(repo_dir: Path, branch: str, file_path: str) -> str:
    """
    Read file content from a git branch without checking it out.

    Uses git show to read the file content directly from the git object database.

    Args:
        repo_dir: Path to the git repository
        branch: Branch name or git ref to read from
        file_path: Path to the file relative to repository root

    Returns:
        File content as string

    Raises:
        ValueError: If git command fails or file doesn't exist in branch
    """
    try:
        # Use git show to read file content
        git_path = f"{branch}:{file_path}"
        result = subprocess.run(
            ['git', 'show', git_path],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise ValueError(f"Error reading file '{file_path}' from git branch '{branch}': {error_msg}")
    except Exception as e:
        raise ValueError(f"Unexpected error reading file from git: {e}")


def extract_issue_key(issue_url: str) -> str:
    """
    Extract Jira issue key from issue URL.

    Args:
        issue_url: Full Jira issue URL (e.g., "https://issues.redhat.com/browse/RHOAIENG-12345")

    Returns:
        Issue key (e.g., "RHOAIENG-12345") or "XXX" if URL is invalid or empty
    """
    if not issue_url:
        return "XXX"

    # Extract the part after /browse/
    if '/browse/' in issue_url:
        return issue_url.split('/browse/')[-1]

    # If it's already just the key, return it
    if '-' in issue_url and not '/' in issue_url:
        return issue_url

    return "XXX"


def detect_accelerator(component_name: str, accelerator_rules: dict) -> Optional[str]:
    """
    Detect which accelerator (if any) is used by a component based on its name.

    Returns:
        Accelerator name if detected, None otherwise
    """
    component_lower = component_name.lower()
    for accelerator in accelerator_rules.keys():
        if accelerator in component_lower:
            return accelerator
    return None


def get_exception_for_arch(component_name: str, arch: str, config: dict) -> Optional[dict]:
    """
    Check if there's a specific exception for this component/architecture combination.

    Args:
        component_name: Name of the component
        arch: Architecture to check
        config: Configuration dict with exceptions

    Returns:
        Exception dict if found, None otherwise
    """
    exceptions = config.get('exception', [])
    for exception in exceptions:
        if exception.get('component') == component_name:
            if arch in exception.get('architectures', []):
                return exception
    return None


def is_accelerator_incompatible(component_name: str, arch: str, config: dict) -> bool:
    """
    Check if architecture is incompatible due to accelerator rules.

    Args:
        component_name: Name of the component
        arch: Architecture to check
        config: Configuration dict with accelerator_incompatibility_rules

    Returns:
        True if accelerator incompatibility rule applies, False otherwise
    """
    accelerator_rules = config.get('accelerator_incompatibility_rules', {})
    detected_accelerator = detect_accelerator(component_name, accelerator_rules)

    if detected_accelerator:
        incompatible_archs = accelerator_rules.get(detected_accelerator, [])
        if arch in incompatible_archs:
            return True

    return False


def get_cell_value(component_name: str, arch: str, built_archs: Set[str], config: dict, output_format: str) -> str:
    """
    Determine the cell value for a component/architecture combination.

    Args:
        component_name: Name of the component
        arch: Architecture to check
        built_archs: Set of architectures the component is actually built for
        config: Configuration dict with exceptions and accelerator rules
        output_format: Output format ('markdown', 'jira', 'csv', 'text')

    Returns:
        Cell value as string
    """
    # If component is built for this arch, return Y
    if arch in built_archs:
        return 'Y'

    # Check for specific exception first
    exception = get_exception_for_arch(component_name, arch, config)
    if exception:
        # Get issue key from exception
        issue_url = exception.get('issue', '')
        issue_key = extract_issue_key(issue_url)

        # Format based on output type
        if output_format == 'markdown' and issue_url and issue_key != 'XXX':
            return f'[{issue_key}]({issue_url})'
        elif output_format == 'jira' and issue_url and issue_key != 'XXX':
            return f'[{issue_key}|{issue_url}]'
        elif output_format == 'csv' and issue_url and issue_key != 'XXX':
            return f'=HYPERLINK("{issue_url}","{issue_key}")'
        else:
            return issue_key

    # Check for accelerator incompatibility
    if is_accelerator_incompatible(component_name, arch, config):
        return 'N/A'

    # Not built and no exception
    return ''


def generate_table(components: Dict[str, Set[str]], config: dict, output_format: str = 'markdown') -> str:
    """
    Generate architecture support table.

    Args:
        components: Dict mapping component names to sets of supported architectures
        config: Configuration dict with exceptions and accelerator rules
        output_format: 'markdown', 'csv', 'text', or 'jira'

    Returns:
        Formatted table as string
    """
    # Define architecture columns in order
    arch_columns = ['amd64', 'arm64', 'ppc64le', 's390x']

    # Sort components alphabetically
    sorted_components = sorted(components.items())

    if output_format == 'csv':
        lines = ['Component Image,amd64,arm64,ppc64le,s390x']
        for name, archs in sorted_components:
            row = [name]
            for arch in arch_columns:
                cell_value = get_cell_value(name, arch, archs, config, 'csv')
                # Wrap cells containing formulas in quotes and escape internal quotes
                if cell_value.startswith('='):
                    # Escape quotes by doubling them for CSV
                    escaped_value = cell_value.replace('"', '""')
                    row.append(f'"{escaped_value}"')
                else:
                    row.append(cell_value)
            lines.append(','.join(row))
        return '\n'.join(lines)

    elif output_format == 'jira':
        lines = []

        # Header with || for Jira wiki markup
        header = '|| Component Image || amd64 || arm64 || ppc64le || s390x ||'
        lines.append(header)

        # Rows with |
        for name, archs in sorted_components:
            row_data = [name]
            for arch in arch_columns:
                row_data.append(get_cell_value(name, arch, archs, config, 'jira'))
            lines.append('| ' + ' | '.join(row_data) + ' |')

        return '\n'.join(lines)

    elif output_format == 'markdown':
        # Calculate column widths
        max_name_len = max(len(name) for name, _ in sorted_components) if sorted_components else 10
        max_name_len = max(max_name_len, len('Component Image'))

        # Calculate max width for each architecture column
        arch_widths = {}
        for arch in arch_columns:
            max_width = len(arch)
            for name, archs in sorted_components:
                cell_value = get_cell_value(name, arch, archs, config, 'markdown')
                # For markdown links, the display width is just the link text part
                if cell_value.startswith('[') and '](' in cell_value:
                    display_text = cell_value.split(']')[0][1:]
                    max_width = max(max_width, len(display_text))
                else:
                    max_width = max(max_width, len(cell_value))
            arch_widths[arch] = max_width

        # Build table
        lines = []

        # Header
        header_parts = [f"{'Component Image':<{max_name_len}}"]
        for arch in arch_columns:
            header_parts.append(f"{arch:^{arch_widths[arch]}}")
        header = '| ' + ' | '.join(header_parts) + ' |'

        # Separator
        sep_parts = ['-' * max_name_len]
        for arch in arch_columns:
            sep_parts.append('-' * arch_widths[arch])
        separator = '|' + '|'.join(f" {s} " for s in sep_parts) + '|'

        lines.append(header)
        lines.append(separator)

        # Rows
        for name, archs in sorted_components:
            row_parts = [name.ljust(max_name_len)]
            for arch in arch_columns:
                cell_value = get_cell_value(name, arch, archs, config, 'markdown')
                # Center the cell value
                row_parts.append(f"{cell_value:^{arch_widths[arch]}}")
            lines.append('| ' + ' | '.join(row_parts) + ' |')

        return '\n'.join(lines)

    else:  # text format
        # Calculate column widths
        max_name_len = max(len(name) for name, _ in sorted_components) if sorted_components else 10
        max_name_len = max(max_name_len, len('Component Image'))

        # Calculate max width for each architecture column
        arch_widths = {}
        for arch in arch_columns:
            max_width = len(arch)
            for name, archs in sorted_components:
                cell_value = get_cell_value(name, arch, archs, config, 'text')
                max_width = max(max_width, len(cell_value))
            arch_widths[arch] = max_width

        lines = []

        # Header
        header_parts = [f"{'Component Image':<{max_name_len}}"]
        for arch in arch_columns:
            header_parts.append(f"{arch:^{arch_widths[arch]}}")
        header = '  '.join(header_parts)

        separator = '-' * len(header)
        lines.append(header)
        lines.append(separator)

        # Rows
        for name, archs in sorted_components:
            row_parts = [f"{name:<{max_name_len}}"]
            for arch in arch_columns:
                cell_value = get_cell_value(name, arch, archs, config, 'text')
                row_parts.append(f"{cell_value:^{arch_widths[arch]}}")
            lines.append('  '.join(row_parts))

        return '\n'.join(lines)


def main():
    """Main function."""
    import argparse

    # Auto-detect script location and repository root
    SCRIPT_DIR = Path(__file__).parent.resolve()

    # Auto-detect repository root based on script location
    if SCRIPT_DIR.name == 'multi-arch-tracking' and SCRIPT_DIR.parent.name == 'script':
        # Script is in expected location: script/multi-arch-tracking/
        # Repository root is 2 levels up
        DEFAULT_BASE_DIR = SCRIPT_DIR.parent.parent
    else:
        # Script in unknown location, use current directory
        DEFAULT_BASE_DIR = Path.cwd()

    # Config file always lives next to the script
    DEFAULT_CONFIG = SCRIPT_DIR / 'exceptions.toml'

    parser = argparse.ArgumentParser(
        description='Generate architecture support table from PipelineRun YAML files'
    )
    parser.add_argument(
        '--base-dir',
        type=Path,
        default=DEFAULT_BASE_DIR,
        help='Base directory of the konflux-central repository (default: auto-detected from script location)'
    )
    parser.add_argument(
        '--format',
        choices=['markdown', 'csv', 'text', 'jira'],
        default='markdown',
        help='Output format (default: markdown)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Output file (default: stdout)'
    )
    parser.add_argument(
        '--config',
        type=Path,
        default=DEFAULT_CONFIG,
        help='TOML configuration file for exceptions (default: exceptions.toml in script directory)'
    )
    parser.add_argument(
        '--branch',
        type=str,
        help='Git branch or ref to read PipelineRun files from (e.g., rhoai-3.2). '
             'If specified, reads files from git without checking out the branch. '
             'If omitted, reads from the current filesystem.'
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)
    if args.config.exists():
        print(f"Loaded configuration from {args.config}", file=sys.stderr)
    else:
        print(f"Configuration file {args.config} not found, using defaults", file=sys.stderr)

    # Parse all files - use git strategy if branch specified, otherwise filesystem
    components = {}

    if args.branch:
        # Git-based reading strategy
        print(f"Reading PipelineRun files from git branch '{args.branch}'", file=sys.stderr)

        # Validate branch exists
        if not validate_git_branch(args.base_dir, args.branch):
            print(f"Error: Git branch '{args.branch}' not found.", file=sys.stderr)
            print(f"Tip: Run 'git fetch origin' to update remote branches.", file=sys.stderr)
            sys.exit(1)

        # Find files in git branch
        try:
            file_paths = find_pipelinerun_files_from_git(args.base_dir, args.branch)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if not file_paths:
            print(f"Error: No PipelineRun files found in branch '{args.branch}' "
                  f"matching pattern pipelineruns/*/.tekton/*.yaml", file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(file_paths)} PipelineRun files", file=sys.stderr)

        # Read and parse each file from git
        for file_path in file_paths:
            try:
                content = read_file_from_git(args.base_dir, args.branch, file_path)
                component_name, architectures = parse_pipelinerun_from_content(file_path, content)
                if component_name and architectures:
                    components[component_name] = architectures
            except ValueError as e:
                print(f"Warning: {e}", file=sys.stderr)

    else:
        # Filesystem-based reading strategy (original behavior)
        print(f"Reading PipelineRun files from filesystem", file=sys.stderr)

        files = find_pipelinerun_files(args.base_dir)

        if not files:
            print(f"Error: No PipelineRun files found in {args.base_dir}/pipelineruns/*/.tekton/",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(files)} PipelineRun files", file=sys.stderr)

        # Parse all files
        for file_path in files:
            component_name, architectures = parse_pipelinerun(file_path)
            if component_name and architectures:
                components[component_name] = architectures

    print(f"Parsed {len(components)} components", file=sys.stderr)

    # Generate table
    table = generate_table(components, config, args.format)

    # Output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(table)
            f.write('\n')
        print(f"Table written to {args.output}", file=sys.stderr)
    else:
        print(table)


if __name__ == '__main__':
    main()
