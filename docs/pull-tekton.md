# Pull Tekton

Pull `.tekton/` pipeline definitions from a component repository back
into konflux-central. This is the reverse of the `sync-pipelineruns`
workflow, which pushes `.tekton/` from konflux-central out to component
repos.

Only files already tracked in `pipelineruns/<REPO>/.tekton/` are
updated — new files in the remote `.tekton/` are ignored.

## Files

| File | Purpose |
|------|---------|
| `script/pull-tekton.sh` | Fetches `.tekton/` from a remote repo and updates local files |
| `script/pull-tekton-pr.sh` | CI helper — branches, commits, and opens a PR |
| `.github/workflows/pull-tekton.yml` | GitHub Actions workflow wrapping both scripts |
| `docs/pull-tekton.md` | This documentation |

## Local Usage

```bash
# Pull .tekton/ from odh-dashboard at the rhoai-3.4 branch
./script/pull-tekton.sh odh-dashboard rhoai-3.4

# Explicit org
./script/pull-tekton.sh opendatahub-io/odh-dashboard main

# Pull from a specific commit SHA
./script/pull-tekton.sh odh-dashboard abc123def
```

The script prints which files were updated or skipped and suggests a
`git diff` command to review the changes. After reviewing, commit
manually as usual.

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `[ORG/]REPO` | Yes | GitHub repo name. If no org is specified, defaults to `red-hat-data-services`. |
| `REF` | Yes | Branch, tag, or commit SHA to pull from. |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KONFLUX_ROOT` | Parent of script directory | Override the root directory for locating `pipelineruns/`. Used by CI when the script is checked out in a different path than the working repo. |

## GitHub Actions Workflow

The `pull-tekton.yml` workflow provides the same functionality via
GitHub's UI with automatic PR creation.

### Trigger

`workflow_dispatch` only — select the release branch to run on, pick a
repository, and optionally specify a ref.

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `repository` | choice | Yes | Component repository to pull from. Options are kept in sync by `update-repository-list.yml`. |
| `ref` | string | No | Branch, tag, or commit SHA. Defaults to the branch the workflow is dispatched on. |

### What It Does

1. Checks out the selected release branch
2. Checks out `main` to get the latest scripts
3. Runs `pull-tekton.sh` to fetch and update `.tekton/` files
4. Runs `pull-tekton-pr.sh` to create a PR against the release branch

If no files changed, the workflow exits cleanly without creating a PR.

### Repository List Sync

The `repository` choice list is automatically updated by the
`update-repository-list.yml` workflow whenever `pipelineruns/` changes.
It invokes `script/update-sync-pipelinerun-workflow-repository-list.py`
with `--no-all` (since pull-tekton operates on a single repo).

## CI-Agnostic Design

The workflow YAML is a thin wrapper — all logic lives in the shell
scripts. GitHub Actions `${{ }}` expressions are confined to `env:`
mappings; `run:` blocks use only shell variables. To port to another CI
system:

1. Replicate the auth and checkout steps for your platform
2. Set the environment variables listed below
3. Call the two scripts in sequence

### Environment Variables for `pull-tekton-pr.sh`

| Variable | Required | Description |
|----------|----------|-------------|
| `REPOSITORY` | Yes | Repo name (e.g. `odh-dashboard`) |
| `REF` | Yes | Ref that was pulled from |
| `BASE_BRANCH` | Yes | Branch to open the PR against |
| `GITHUB_ORG` | No | Org name (default: `red-hat-data-services`) |
| `CI_JOB_URL` | No | Link to the CI run, included in the PR body |
| `GH_TOKEN` | Yes | GitHub token for `gh pr create` |
