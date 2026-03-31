# Konflux-Central

Central configuration repository for Red Hat OpenShift AI (RHOAI) Konflux CI/CD. This repo manages two synchronization systems that keep RHOAI component repositories consistent: **Pipeline Sync** and **Renovate Sync**.

## Pipeline Sync

### Background

RHOAI is an OpenShift operator built on top of Open Data Hub (ODH). Source code flows through a series of forks:

```
upstream repos (if applicable) → ODH repos → RHOAI repo (main) → RHOAI repo (release branch) → build and delivery
```

Builds run on [Konflux](https://konflux-ci.dev/), which uses Tekton pipelines-as-code. Each component repository has a `.tekton/` directory containing PipelineRun definitions that tell Konflux how to build the component's container image(s).

### How It Works

Rather than managing PipelineRun definitions independently in each of the 48+ component repositories, this repo serves as the **single source of truth**. PipelineRun files are authored and maintained here, then automatically synced to component repos.

```
konflux-central                                    component repos
┌──────────────────────────────────┐               ┌──────────────────────────┐
│ pipelineruns/{component}/.tekton/│  ── sync ──►  │ .tekton/                 │
│   ├── component-v3-4-push.yaml   │               │   ├── component-v3-4-... │
│   └── component-v3-4-pr.yaml     │               │   └── component-v3-4-... │
│                                  │               └──────────────────────────┘
│ pipelines/                       │
│   ├── container-build.yaml       │  ◄── referenced by pipelineruns
│   ├── multi-arch-container-...   │
│   └── fbc-fragment-build.yaml    │
└──────────────────────────────────┘
```

**The `.tekton/` directories in component repos should be considered to be read-only.** All changes must be made here in konflux-central.

### Repository Structure

- **`pipelines/`** — Reusable Tekton pipeline definitions shared by all components
  - `container-build.yaml` — Single-architecture container builds
  - `multi-arch-container-build.yaml` — Multi-platform builds (amd64, arm64, ppc64le, s390x)
  - `fbc-fragment-build.yaml` — File-Based Catalog fragment builds

- **`pipelineruns/`** — Component-specific PipelineRun definitions, organized as `pipelineruns/{component}/.tekton/`
  - Files follow the naming pattern: `{component}-{version}-{trigger}.yaml`
  - Triggers: `push` (builds on merge), `pull-request` (validates PRs), `scheduled` (nightly/periodic)

- **`.tekton/`** — Pull request pipelines for this repo itself (canary builds to validate pipeline changes)

### Branch Structure

| Branch | Purpose |
|--------|---------|
| `main` | Development branch. Contains pull request pipelines and tooling only — no push pipelines. |
| `rhoai-X.Y` | Release branches (e.g., `rhoai-2.16`, `rhoai-3.4`). Contain push and PR pipelineruns for all components in that release. Z-stream releases (e.g., `v2.16.5`) continue on the same `rhoai-X.Y` branch — there is no separate branch per patch version. See [Z-Stream Updates](#z-stream-updates) for how to bump the version. |
| `rhoai-X.Y-ea.N` | Early access release branches (e.g., `rhoai-3.4-ea.2`). Same structure as release branches. |
Unlike source code repos, changes on `main` do **not** flow to release branches. Each branch is independent.

### Sync Mechanism

The [sync-pipelineruns](.github/workflows/sync-pipelineruns.yml) GitHub Actions workflow runs automatically when files under `pipelineruns/` are pushed to `main` or any `rhoai-*` branch. It:

1. Detects which component directories changed
2. Generates a sync matrix via `generate_pipelinerun_sync_config.py`
3. Copies each component's `.tekton/` directory to the corresponding component repository
4. Commits with a message linking back to the triggering commit

The workflow can also be triggered manually via `workflow_dispatch` for selective syncing or dry-run testing. The component dropdown in the workflow dispatch UI is automatically kept up to date by the [update-repository-list](.github/workflows/update-repository-list.yml) workflow, which runs whenever `pipelineruns/` changes.

To skip sync on a commit, include `[skip-sync]` in the commit message.

### Creating a New Release Branch

The [pipelinerun-replicator](.github/workflows/pipelinerun-replicator.yml) workflow automates creation of new release branches:

1. Takes a source branch (e.g., `rhoai-3.3`) and target version (e.g., `rhoai-3.4`, `v3.4.0`)
2. Copies all pipelinerun files, updating version references, file names, and labels
3. Commits with `[skip-sync]` to prevent immediate sync

### Z-Stream Updates

The [apply-z-stream-changes](.github/workflows/apply-z-stream-changes.yml) workflow increments patch versions (e.g., `v3.4.0` → `v3.4.1`) across all pipelinerun version labels in a release branch.

### Adding a New Component

1. Create `pipelineruns/{component-name}/.tekton/`
2. Add PipelineRun YAML files following the naming convention
3. Reference the appropriate pipeline from `pipelines/`
4. Configure trigger annotations for push/PR events
5. Push to the appropriate release branch — the sync workflow will distribute the files

### Testing Pipeline Changes

- PRs to `rhoai-*` branches trigger validation pipelines in `.tekton/` (canary builds)
- The [validate-pipelineruns](.github/workflows/validate-pipelineruns.yml) workflow runs structural validation on PRs — see [docs/validate-pipelineruns.md](docs/validate-pipelineruns.md) for details
---

## Renovate Sync

Centralized management and synchronization of [Renovate](https://docs.renovatebot.com/) configuration files across RHOAI component repositories. Renovate automates dependency update PRs (e.g., base image digest bumps, Tekton task bundle updates).

### How It Works

1. **Author configs** in the `renovate/` directory of this repository
2. **Map configs to repos** by editing `config.yaml`
3. **Run the sync** via the [sync-renovate-configs](.github/workflows/sync-renovate-configs.yml) GitHub Actions workflow (manual trigger)

### Available Configs

| Config | Purpose | Target Repos |
|--------|---------|--------------|
| `default-renovate.json5` | Standard RHOAI renovate config. Auto-merges digest-only updates for `Dockerfile.konflux` files, tracks RPM updates. | Most RHOAI component repos (~35) |
| `custom-renovate.json5` | Tracks container image digests in `additional-images-patch.yaml` files. | RHOAI-Build-Config |
| `llama-stack-renovate.json5` | Tracks base images, PyPI packages, and GitLab wheel artifacts. | llama-stack-distribution |
| `pipelines-renovate.json5` | Maintains Tekton task bundle references in pipeline YAML files. Digest-only updates, auto-merge. | This repo (konflux-central) |

The `.json5` source files are compiled to `.json` distribution files for sync.

### Configuring `config.yaml`

```yaml
- renovate-config: "renovate/default-renovate-distribution.json"
  sync-repositories:
    - name: "red-hat-data-services/trustyai-explainability"
    - name: "red-hat-data-services/argo-workflows"
      targetFilePath: "renovate.json"  # optional, default: .github/renovate.json

- renovate-config: "renovate/custom-renovate-distribution.json"
  sync-repositories:
    - name: "red-hat-data-services/RHOAI-Build-Config"
```

- **`renovate-config`**: Path to the distribution JSON file in `renovate/`
- **`sync-repositories`**: List of target repositories
- **`targetFilePath`** (optional): Custom destination path for the config file. Defaults to `.github/renovate.json`
