# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository (`konflux-central`) serves as the central configuration repository for Red Hat OpenShift AI (RHOAI) Konflux CI/CD pipelines. It manages Tekton pipelines and PipelineRuns for building container images across multiple RHOAI components.

## Repository Structure

- `pipelines/` - Reusable Tekton pipeline definitions
  - `container-build.yaml` - Single-arch container build pipeline
  - `multi-arch-container-build.yaml` - Multi-platform container build pipeline
  - `fbc-fragment-build.yaml` - File-Based Catalog fragment build pipeline

- `pipelineruns/` - Component-specific PipelineRun definitions organized by component name
  - Each component directory contains a `.tekton/` subdirectory
  - PipelineRun files follow the naming pattern: `{component}-{version}-{trigger}.yaml`
  - Example: `pipelineruns/odh-dashboard/.tekton/odh-dashboard-v3-2-push.yaml`

- `.tekton/` - Pull request pipeline definitions for this repository itself
  - `container-build-pull-request.yaml` - Triggered on PRs to validate pipeline changes
  - `multi-arch-container-build-pull-request.yaml` - Multi-arch validation for PRs

- `canary-build/` - Canary build resources for testing pipeline changes
  - `Dockerfile.konflux` - Minimal Dockerfile used for canary builds

## Branch Structure

- Branches follow the pattern `rhoai-X.Y` (e.g., `rhoai-3.2`)
- Each branch corresponds to a specific RHOAI release version
- The `main` branch is the primary development branch
- PipelineRuns are typically configured to reference pipelines from the same target branch

## Pipeline Architecture

### Single-Architecture Pipeline (`container-build.yaml`)

Used for building container images on a single platform. The pipeline:
1. Clones the source repository using `git-clone-oci-ta` task
2. Prefetches dependencies with `prefetch-dependencies-oci-ta` task (supports npm, gomod, rpm via Cachi2)
3. Builds the container image with `buildah-oci-ta` task
4. Creates an image index with `build-image-index` task
5. Builds a source image with `source-build-oci-ta` task
6. Runs security and quality checks:
   - `sast-shell-check-oci-ta` - Shell script static analysis
   - `sast-unicode-check-oci-ta` - Unicode character validation
   - `deprecated-base-image-check` - Base image deprecation check
   - `clair-scan` - Vulnerability scanning
   - `ecosystem-cert-preflight-checks` - Red Hat certification checks
   - `sast-snyk-check-oci-ta` - Snyk security scanning
   - `clamav-scan` - Malware scanning
   - `rpms-signature-scan` - RPM signature validation
7. Applies additional tags with `apply-tags` task
8. Pushes the Dockerfile with `push-dockerfile-oci-ta` task
9. Sends Slack notifications on failure via `send-slack-notification` task

### Multi-Architecture Pipeline (`multi-arch-container-build.yaml`)

Extends the single-arch pipeline to support multiple platforms (x86_64, ppc64le, s390x, arm64). Uses the `build-platforms` parameter to specify target architectures.

### PipelineRun Configuration

PipelineRuns are configured with:
- **Triggers**: Defined via `pipelinesascode.tekton.dev/on-cel-expression` annotations
  - Push events: `event == "push" && target_branch == "rhoai-X.Y"`
  - Pull request events: `event == "pull_request" && target_branch.matches("^rhoai-\\d+\\.\\d+$")`
- **Parameters**:
  - `git-url` - Source repository URL (templated with `{{source_url}}`)
  - `revision` - Git commit SHA (templated with `{{revision}}`)
  - `output-image` - Target image registry path (typically `quay.io/rhoai/...`)
  - `dockerfile` - Path to Dockerfile (often `Dockerfile.konflux`)
  - `path-context` - Build context directory
  - `hermetic` - Network isolation flag (usually `true`)
  - `prefetch-input` - Dependency prefetch configuration (JSON array)
  - `build-platforms` - Array of target platforms for multi-arch builds
  - `additional-tags` - Extra image tags beyond the default
  - `additional-labels` - Extra OCI image labels

### Custom RHOAI Tasks

The pipelines use custom RHOAI-specific tasks from the `rhoai-konflux-tasks` repository:
- `rhoai-init` - Initialization task that validates cluster environment and prepares Slack notifications
  - Located at: `https://github.com/red-hat-data-services/rhoai-konflux-tasks.git`
  - Referenced by commit SHA in pipeline definitions
  - Sets `expected-cluster` parameter to ensure builds run on the correct cluster

## GitHub Actions Workflows

### Sync Pipelineruns (`.github/workflows/sync-pipelineruns.yml`)

Automates the synchronization of PipelineRun definitions to component repositories:
- Triggered on pushes to `rhoai-*` branches when files in `pipelineruns/` change
- Can be manually triggered with `workflow_dispatch` to select specific repositories
- Supports dry-run mode for testing without committing changes
- Manages synchronization for all RHOAI components

### Update Repository List (`.github/workflows/update-repository-list.yml`)

Maintains the list of repositories available in the sync workflow.

## Working with PipelineRuns

### Adding a New Component

1. Create a new directory under `pipelineruns/{component-name}/`
2. Create a `.tekton/` subdirectory
3. Add PipelineRun YAML files following the naming convention
4. Reference the appropriate pipeline from `pipelines/` directory
5. Configure trigger expressions for push/PR events
6. Set component-specific parameters (image name, dockerfile path, etc.)

### Modifying Existing Pipelines

- Pipeline changes in `pipelines/` directory affect all components using those pipelines
- Test changes using the canary-build pipeline triggers in `.tekton/` before merging
- Pipeline definitions reference specific task bundle digests for reproducibility
- Update task bundle references when updating to newer task versions

### Testing Pipeline Changes

- Pull requests to `rhoai-*` branches trigger validation pipelines in `.tekton/`
- Canary builds validate pipeline modifications before they affect component builds
- Use `[skip-sync]` in commit messages to prevent automatic PipelineRun synchronization

## Common Parameters and Conventions

- **Image Registry**: Most images are pushed to `quay.io/rhoai/` or `quay.io/redhat-user-workloads/rhoai-tenant/`
- **Image Tags**: Follow the pattern `{branch}-{commit-sha}` for additional tags
- **Version Labels**: Set via `additional-labels` parameter (e.g., `version=v3.2.0`)
- **Timeouts**: Multi-arch builds typically use 8-hour pipeline timeouts
- **Service Accounts**: Each component has a dedicated service account (e.g., `build-pipeline-odh-dashboard-v3-2`)
- **Workspaces**: Git authentication is provided via the `git-auth` workspace using secrets

## Hermetic Builds and Dependency Prefetching

- Most builds use hermetic mode (`hermetic: true`) for network isolation
- Dependencies are prefetched using Cachi2 via the `prefetch-input` parameter
- Supported package managers: npm, gomod, rpm, pip
- Prefetch configuration is passed as JSON array in the PipelineRun spec

## Security and Compliance

- All builds include comprehensive security scanning (Clair, Snyk, ClamAV)
- RPM signatures are validated for Red Hat package integrity
- Base images are checked for deprecation
- Static analysis is performed on shell scripts
- Red Hat ecosystem certification checks ensure compliance
- Build failures trigger Slack notifications to the RHOAI team

## Architecture Support Table Generator

A Python script (`script/multi-arch-tracking/generate-table.py`) that generates tables showing which CPU architectures each RHOAI component supports.

### Usage

```bash
# Generate architecture support table
./script/multi-arch-tracking/generate-table.py --format markdown

# Output formats: markdown, csv, jira, text
./script/multi-arch-tracking/generate-table.py --format csv --output arch_support.csv
```

### Google Sheets Integration

When using CSV format, the output includes `=HYPERLINK()` formulas for Jira issue references:
- The CSV is formatted with proper escaping for Google Sheets import
- After importing the CSV into Google Sheets (File → Import):
  1. Select all cells containing formulas
  2. Go to **Format → Number → Automatic**
  3. Formulas will activate and become clickable hyperlinks
- Note: Formulas are imported as plain text initially and require manual activation

### Configuration

The `script/multi-arch-tracking/exceptions.toml` file tracks:
- **Accelerator incompatibility rules**: Defines which architectures are incompatible with each accelerator type (CUDA, ROCm, Gaudi, Spyre)
- **Specific exceptions**: Tracks component/architecture combinations that cannot be built, with Jira issue references

### Cell Values in Tables

- **Y** - Component is built for this architecture
- **N/A** - Accelerator incompatibility (e.g., CUDA not available on ppc64le)
- **Issue Key** (e.g., RHOAIENG-12345) - Specific exception tracked in Jira
- **XXX** - Exception without assigned Jira issue
- **(empty)** - Not currently built, but architecturally possible

### Common Architecture Patterns

- **Core platform components**: Built for all 4 architectures (amd64, arm64, ppc64le, s390x)
- **CUDA components**: Only amd64 and sometimes arm64 (NVIDIA GPU requirement)
- **ROCm components**: Only amd64 (AMD GPU requirement)
- **Gaudi/Spyre components**: Only amd64 (Intel accelerator requirement)
- **Data Science Pipelines**: amd64, arm64, ppc64le (no s390x)

See `script/multi-arch-tracking/README.md` for complete documentation.
