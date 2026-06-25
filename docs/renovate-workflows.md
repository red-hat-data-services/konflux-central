# Renovate Workflows

GitHub Actions workflows for running Renovate dependency updates against
RHOAI component repositories. These complement MintMaker (the hosted
Renovate instance in Konflux that runs on a 4-hour schedule) by providing
on-demand runs and PR-level dry-run feedback.

## Overview

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| Run Renovate | `workflow_dispatch` | On-demand Renovate runs (live or dry-run) against any repo |
| PR Check: Renovate Dry Run | `pull_request` | Dry-run against repos affected by a config change |
| Post PR Check: Renovate Dry Run Comment | `workflow_run` | Posts dry-run results as a PR comment |

### Files

| File | Purpose |
|------|---------|
| `.github/workflows/run-renovate.yml` | On-demand workflow |
| `.github/workflows/renovate-dry-run.yml` | PR-triggered dry-run workflow |
| `.github/workflows/post-renovate-dry-run-comment.yml` | Posts dry-run results as PR comment |
| `script/run-renovate.sh` | Runs Renovate in a podman container with MintMaker config layering |
| `script/generate-renovate-matrix.py` | Builds repo/branch matrix from `config.yaml` |
| `script/detect-affected-renovate-repos.py` | Maps changed files to affected repos |
| `script/extract-renovate-dry-run-results.py` | Parses Renovate JSON logs for dependency info |
| `script/update-renovate-workflow-repository-list.py` | Maintains the repo dropdown in `run-renovate.yml` |

## Run Renovate

**Workflow:** `.github/workflows/run-renovate.yml`
**Trigger:** Manual (`workflow_dispatch`) from the Actions tab

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `repository` | choice | — | Target repo (from `config.yaml`) or `all` |
| `branches` | string | *(empty)* | Comma-separated branch override; empty uses `baseBranches` from the renovate config |
| `dry_run` | boolean | `true` | When true, Renovate simulates but creates no PRs |
| `log_level` | choice | `debug` | Renovate log verbosity: debug, info, warn |

### Jobs

**`setup`** — Builds the matrix:
1. Checks out the repo at the current ref (supports running from feature branches)
2. Runs `generate-renovate-matrix.py` to map repos to configs and parse `baseBranches`
3. Outputs a JSON matrix for the next job

**`renovate`** (matrix) — Runs Renovate per repo:
1. Creates a GitHub App token scoped to the target repo
2. Installs `json5` (needed for config wrapper generation)
3. Calls `run-renovate.sh` which generates a wrapper config, pulls the
   mintmaker image, and runs Renovate
4. Extracts PR numbers from the log, writes a step summary, and comments
   on any created PRs with a link to the workflow run

### Usage

From the Actions tab:
1. Select **Run Renovate**
2. Pick a repository (or `all`)
3. Optionally specify branches and change dry-run/log-level
4. Click **Run workflow**

The workflow can also be run from a feature branch — it checks out
`github.ref_name`, so renovate configs from that branch are used.

## PR Check: Renovate Dry Run

**Workflow:** `.github/workflows/renovate-dry-run.yml`
**Trigger:** Pull requests to `main` that modify:
- `renovate/**`
- `config.yaml`
- `.github/renovate.json`

Automatically detects which repos are affected by the config change and
runs Renovate in dry-run mode against each one.

### Jobs

**`setup`** — Detects affected repos:
1. Uses `detect-affected-renovate-repos.py` to map changed files to repos
2. If `config.yaml` changed, all repos are affected
3. If only specific configs changed, only repos using those configs are tested
4. Builds the matrix via `generate-renovate-matrix.py`

**`dry-run`** (matrix) — Runs Renovate dry-run per affected repo:
1. Runs with `--dry-run --log-format json`
2. Parses JSON logs with `extract-renovate-dry-run-results.py` to find
   dependency updates
3. Uploads per-repo markdown results as artifacts

**`report`** — Consolidates results:
1. Downloads all per-repo result artifacts
2. Builds a markdown table: Repo | Config | Branches | Result
3. Repos with updates get an expandable details section listing each
   dependency and its affected branches
4. Uploads the consolidated report as a `renovate-dry-run-comment` artifact

### PR Comment

A companion workflow (`post-renovate-dry-run-comment.yml`) posts the
report as a PR comment. It uses the `workflow_run` trigger pattern
(same as `post-validation-comment.yml`) so it works for fork PRs.

The comment is identified by a `<!-- renovate-dry-run-comment -->` HTML
marker and updated in place on subsequent runs.

## Config Layering

In production, MintMaker applies two config layers:
1. **Global config** — [MintMaker's `renovate.json`](https://github.com/konflux-ci/mintmaker/blob/main/config/renovate/renovate.json),
   which includes tekton task bundle grouping, branch prefix conventions,
   post-upgrade migration tasks, and other defaults
2. **Repo config** — the repo's own `renovate.json` (e.g., `.github/renovate.json`),
   which extends a source config from this repo (e.g., `renovate/pipelines-renovate.json5`)

`run-renovate.sh` mimics this stack when `--config-ref` is provided.
It generates a pure-extends wrapper config:

```json
{
  "extends": [
    "github>konflux-ci/mintmaker//config/renovate/renovate.json",
    "github>red-hat-data-services/konflux-central//renovate/pipelines-renovate.json5#<sha>"
  ]
}
```

MintMaker's config is extended first, then our source config is extended
second. Because `enabledManagers` is an unmergeable field in Renovate,
our value replaces MintMaker's full list (~60 managers) rather than
merging with it.

Without `--config-ref`, the script uses the local config file directly
with no MintMaker layering — useful for quick local testing.

### Inherited MintMaker Settings

The following settings are inherited from
[MintMaker's global config](https://github.com/konflux-ci/mintmaker/blob/main/config/renovate/renovate.json)
via the `extends` mechanism (when `--config-ref` is provided). These do
not need to be duplicated in our source configs:

| Setting | Value | Effect |
|---------|-------|--------|
| `groupName` | `"Konflux references"` | Groups tekton task bundle updates into a single PR per branch |
| `postUpgradeTasks` | `pipeline-migration-tool` | Runs the pipeline migration tool after task bundle updates |
| `branchPrefix` | `"konflux/mintmaker/"` | PR branches follow `konflux/mintmaker/{baseBranch}/...` convention |
| `additionalBranchPrefix` | `"{{baseBranch}}/"` | Adds the target branch name to the PR branch |
| `minimumReleaseAge` | `"3 days"` | Waits 3 days before proposing new releases |
| `pruneStaleBranches` | `true` | Cleans up branches for closed/merged PRs |
| Replacement rules | *(various)* | Migrates references from old tekton catalog locations to new ones |

### Distribution Config Resolution

Distribution configs (e.g., `default-renovate-distribution.json`) are
thin wrappers that `extends` a source config via a `github>` remote
reference. The `github>` reference always resolves from the default
branch (main), so feature branch changes wouldn't be tested.

`generate-renovate-matrix.py` handles this by resolving distribution
configs to their local source config path. This way, running the
workflow from a feature branch uses the local version of the config.

## Credentials

Both workflows use a GitHub App (`APP_ID` and `PRIVATE_KEY` secrets)
to create tokens scoped to individual target repos. Each matrix job
creates its own token with `repositories: <short-name>`, following
the principle of least privilege.

The token is passed to Renovate via the `RENOVATE_TOKEN` environment
variable (not a CLI argument, to avoid exposure in process listings).

## Scripts

### `script/run-renovate.sh`

Runs Renovate in a podman container against a single repository.

```bash
RENOVATE_TOKEN=<token> ./script/run-renovate.sh \
  --repo <org/repo> \
  --config-file <path/to/config.json5> \
  [--config-ref <sha>] \
  [--image <image>] \
  [--branches '["main","rhoai-3.4"]'] \
  [--dry-run] \
  [--log-level debug] \
  [--log-format json] \
  [--no-pull]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | *(required)* | Target repository (e.g., `red-hat-data-services/odh-dashboard`) |
| `--config-file` | *(required)* | Path to the Renovate config file |
| `--config-ref` | *(none)* | Git ref (SHA/branch) to fetch the config from GitHub. Enables MintMaker config layering. Omit for local-only testing |
| `--image` | `quay.io/konflux-ci/mintmaker-renovate-image:latest` | Renovate container image |
| `--branches` | `[]` | JSON array of base branches (empty = use config's `baseBranches`) |
| `--dry-run` | `false` | Run in dry-run mode |
| `--log-level` | `debug` | Renovate log level |
| `--log-format` | *(unset)* | Set to `json` for structured log output |
| `--no-pull` | `false` | Skip pulling the image (use `--pull=never`) |

With `--config-ref`, the script:
1. Builds a pure-extends wrapper referencing MintMaker's global config
   first, then the source config at the given ref second
2. Runs `podman run` with the wrapper mounted as `RENOVATE_CONFIG_FILE`

Without `--config-ref`, the script uses the local config file directly
with no MintMaker layering.

### `script/generate-renovate-matrix.py`

Builds the repo/branch matrix from `config.yaml`.

```bash
python3 script/generate-renovate-matrix.py \
  --config-file config.yaml \
  --org red-hat-data-services \
  --repository all \
  --branches ""
```

Outputs compact JSON to stdout, diagnostic info to stderr. Resolves
distribution configs to source configs for feature branch testing.

### `script/detect-affected-renovate-repos.py`

Maps changed file paths to affected repos. Reads file paths from
stdin or positional arguments.

```bash
echo "renovate/default-renovate.json5" | python3 script/detect-affected-renovate-repos.py
```

Outputs: `all` (if `config.yaml` changed), `none` (no renovate
impact), or newline-separated repo short names.

### `script/extract-renovate-dry-run-results.py`

Parses Renovate JSON logs (from `--log-format json`) to extract
dependency update information.

```bash
python3 script/extract-renovate-dry-run-results.py \
  --repo odh-dashboard \
  --config renovate/default-renovate.json5 \
  --branches '["main","rhoai-3.5"]' \
  --log /tmp/renovate.log \
  --output /tmp/result.md
```

Looks for `"flattened updates found"` messages in the JSON log,
extracts dependency names and base branches, and writes a markdown
table row with optional expandable details.

### `script/update-renovate-workflow-repository-list.py`

Maintains the repository dropdown in `run-renovate.yml` from `config.yaml`.
Called by `update-repository-list.yml` when `config.yaml` changes on `main`.

```bash
python3 script/update-renovate-workflow-repository-list.py \
  --config-file config.yaml \
  --workflow-file .github/workflows/run-renovate.yml
```

Uses `ruyaml` for round-trip YAML editing to preserve formatting.

## Repo Dropdown Maintenance

The `repository` choice list in `run-renovate.yml` is auto-maintained:
- `update-repository-list.yml` triggers on pushes to `main` that change
  `config.yaml` or `pipelineruns/**`
- It runs `update-renovate-workflow-repository-list.py` to sync the
  dropdown with repos in `config.yaml`
- The list always includes `all` (first) and `konflux-central`, plus
  all repos from `config.yaml`, sorted alphabetically
