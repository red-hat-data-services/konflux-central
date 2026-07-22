# Architecture Support Table Generator

This script generates a table showing which CPU architectures each RHOAI component is built for.

## Requirements

- Python 3.11+ (for `tomllib` support)
- PyYAML library (`python3-yaml` package on Fedora/RHEL)

## Quick Start

The script can be run from either the repository root or from its own directory:

```bash
# From repository root
./script/multi-arch-tracking/generate-table.py --format markdown

# Or from the script directory
cd script/multi-arch-tracking
./generate-table.py --format markdown

# Generate CSV
./script/multi-arch-tracking/generate-table.py --format csv

# Generate Jira wiki markup
./script/multi-arch-tracking/generate-table.py --format jira

# Save to file
./script/multi-arch-tracking/generate-table.py --format markdown --output architecture_support.md
```

## How It Works

The script:

1. Auto-detects the repository root (or uses `--base-dir` if specified)
2. Scans all PipelineRun YAML files in `pipelineruns/*/.tekton/`
3. Extracts the `output-image` parameter to get the component name
4. Extracts the `build-platforms` parameter to get supported architectures
5. Applies exception rules from `exceptions.toml` to mark incompatible combinations as "N/A"
6. Generates a table with the results

## Table Cell Values

- **Y** - Component is built for this architecture
- **N/A** - Architecture is not applicable due to accelerator incompatibility (e.g., CUDA on ppc64le)
- **Issue Key** (e.g., `RHOAIENG-12345`) - Specific exception tracked by a Jira issue
- **XXX** - Specific exception without a Jira issue assigned yet
- **(empty)** - Component is not currently built for this architecture, but could be

In Markdown and Jira formats, issue keys are hyperlinked to their Jira tickets.

## Configuration File

The `exceptions.toml` file (located in the same directory as the script) defines:

### Accelerator Incompatibility Rules

Automatically marks architectures as N/A when a component uses an accelerator that's incompatible with that architecture:

```toml
[accelerator_incompatibility_rules]
rocm = ["arm64", "ppc64le", "s390x"]  # ROCm only works on amd64
cuda = ["ppc64le", "s390x"]           # CUDA works on amd64 and arm64
gaudi = ["arm64", "ppc64le", "s390x"] # Gaudi only works on amd64
spyre = ["arm64", "ppc64le", "s390x"] # Spyre only works on amd64
cpu = []                              # CPU-only is compatible with all
```

The script detects accelerator types by searching for keywords in component names (case-insensitive).

### Specific Exceptions

Track specific component/architecture combinations that cannot be built:

```toml
[[exception]]
component = "odh-some-component-rhel9"
architectures = ["ppc64le", "s390x"]
reason = "Vendor library not available on these architectures"
issue = "https://issues.redhat.com/browse/RHOAIENG-12345"
```

**Fields:**
- `component`: Component name (as it appears in output-image)
- `architectures`: List of architectures affected
- `reason`: Human-readable explanation (for documentation only, not shown in output)
- `issue`: Jira issue URL (optional). If provided, the issue key will be shown in the table output. If omitted, "XXX" will be shown as a placeholder.

**Important:** Exception entries show the issue key or "XXX" in the table, while accelerator_incompatibility_rules show "N/A".

## Command-Line Options

```
--base-dir PATH       Base directory of the repository (default: auto-detected from script location)
--format FORMAT       Output format: markdown, csv, text, or jira (default: markdown)
--output PATH         Write output to file instead of stdout
--config PATH         Path to TOML config file (default: exceptions.toml in script directory)
--branch BRANCH       Git branch to read files from (e.g., rhoai-3.2) instead of filesystem
```

## Examples

### Generate Markdown table and save to file

```bash
./script/multi-arch-tracking/generate-table.py --format markdown --output docs/architecture_support.md
```

### Generate CSV for spreadsheet import

```bash
./script/multi-arch-tracking/generate-table.py --format csv --output arch_support.csv
```

### Generate Jira table for pasting into Jira tickets

```bash
./script/multi-arch-tracking/generate-table.py --format jira
```

### Generate table from a specific git branch

```bash
./script/multi-arch-tracking/generate-table.py --branch rhoai-3.2 --format markdown
```

### Use custom config file

```bash
./script/multi-arch-tracking/generate-table.py --config custom_exceptions.toml
```

## Sample Output

### Markdown Format

```markdown
| Component                                  | amd64 | arm64 |    ppc64le    |    s390x      |
|--------------------------------------------|-------|-------|---------------|---------------|
| odh-dashboard-rhel9                        |   Y   |   Y   |       Y       |       Y       |
| odh-some-exception-rhel9                   |   Y   |   Y   | [RHOAIENG-123](...) | XXX   |
| odh-workbench-pytorch-cuda-py312-rhel9     |   Y   |       |      N/A      |      N/A      |
| odh-workbench-pytorch-rocm-py312-rhel9     |   Y   |  N/A  |      N/A      |      N/A      |
```

In Markdown format, issue keys are clickable links to the Jira tickets. `XXX` appears when no issue is specified.

### Jira Format

```
|| Component || amd64 || arm64 || ppc64le || s390x ||
| odh-dashboard-rhel9 | Y | Y | Y | Y |
| odh-some-exception-rhel9 | Y | Y | [RHOAIENG-123|https://issues.redhat.com/browse/RHOAIENG-123] | XXX |
| odh-workbench-pytorch-cuda-py312-rhel9 | Y |  | N/A | N/A |
| odh-workbench-pytorch-rocm-py312-rhel9 | Y | N/A | N/A | N/A |
```

In Jira format, issue keys use Jira's link syntax `[KEY|URL]`.

### CSV Format

```csv
Component,amd64,arm64,ppc64le,s390x
odh-dashboard-rhel9,Y,Y,Y,Y
odh-some-exception-rhel9,Y,Y,RHOAIENG-123,XXX
odh-workbench-pytorch-cuda-py312-rhel9,Y,,N/A,N/A
odh-workbench-pytorch-rocm-py312-rhel9,Y,N/A,N/A,N/A
```

In CSV format, issue keys are plain text for easy import into spreadsheets.

## Understanding the Output

### Components Built for All Architectures

Components like operators, dashboards, and CPU-only workbenches typically support all four architectures (amd64, arm64, ppc64le, s390x).

### GPU/Accelerator Components

- **CUDA components**: Only amd64 (and some arm64 for newer GPUs)
  - ppc64le and s390x marked as N/A
- **ROCm components**: Only amd64
  - arm64, ppc64le, and s390x marked as N/A
- **Gaudi components**: Only amd64
  - All other architectures marked as N/A

### Special Cases

- **vllm-cpu**: Only ppc64le and s390x (optimized for IBM architectures)
- **Data Science Pipelines**: amd64, arm64, ppc64le (no s390x support)

## Troubleshooting

### "No module named tomllib"

You need Python 3.11 or later. For Python 3.10 or earlier, install the `tomli` package:

```bash
pip install tomli
```

The script will automatically fall back to using `tomli` if `tomllib` is not available.

### "No module named yaml"

Install PyYAML:

```bash
# Fedora/RHEL
sudo dnf install python3-yaml

# Using pip
pip install pyyaml
```

### No PipelineRun files found

Make sure you're running the script from the root of the `konflux-central` repository, or use `--base-dir` to specify the correct path.
