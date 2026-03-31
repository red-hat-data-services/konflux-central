# PipelineRun Validation

Automated validation checks for PipelineRun YAML files in the
konflux-central repository. Implemented per
[RHOAIENG-55175](https://redhat.atlassian.net/browse/RHOAIENG-55175).

## Overview

A GitHub Actions workflow runs pytest-based validation checks against **all**
PipelineRun files on every pull request. The goal is to catch configuration
errors before they are merged and synced to component repositories.

### Files

| File | Purpose |
|------|---------|
| `script/test_validate_pipelineruns.py` | Pytest-based validation checks |
| `script/conftest.py` | Pytest configuration, CLI options, file discovery |
| `.github/workflows/validate-pipelineruns.yml` | GitHub Actions workflow definition |
| `docs/validate-pipelineruns.md` | This documentation |

## PipelineRun Types

The tests auto-detect three PipelineRun types from annotations and names:

| Type | Detection | Example name suffix |
|------|-----------|-------------------|
| **pull_request** | `pipelinesascode.tekton.dev/on-event` contains `pull_request` | `-on-pull-request-XXXXX` |
| **push** | CEL expression contains `"push"` and name does NOT contain `-on-schedule` | `-on-push` |
| **scheduled** | CEL expression contains `"push"` and name contains `-on-schedule` | `-on-schedule` |

## Checks

### Check 1: YAML Linting (`test_yaml_lint`)

**Applies to:** All files
**Severity:** Error

Validates the file is parseable YAML containing a mapping (dict). Also
verifies `kind: PipelineRun`.

### Check 2: Name Convention (`test_name_convention`)

**Applies to:** All types
**Severity:** Error

Validates `metadata.name` follows the naming pattern:

- **push:** Must end with `-on-push`
- **pull_request:** Must contain `-on-pull-request`
- **scheduled:** Must end with `-on-schedule`

### Check 3: Name Consistency (`test_name_consistency`)

**Applies to:** All types
**Severity:** Error or Warning

Validates `metadata.name` is consistent with the
`appstudio.openshift.io/component` label.

- **push/scheduled:** The name (minus the `-on-push` or `-on-schedule`
  suffix) should start with the component label value. Example: name
  `odh-dashboard-v3-4-on-push` → component label `odh-dashboard-v3-4`.
- **pull_request:** The component label should start with
  `pull-request-pipelines`. Mismatches between the PR name and label
  suffix produce a warning rather than an error.

### Check 4: Branch and Repo Targeting (`test_branch_repo_targeting`)

**Applies to:** push, scheduled only
**Severity:** Error

Validates push/scheduled PipelineRuns target the correct branch and repository:

- The `pipelinesascode.tekton.dev/on-cel-expression` annotation must
  contain `target_branch == "<branch>"` matching the `--branch` argument.
- The `build.appstudio.openshift.io/repo` annotation must be present and
  include `?rev={{revision}}`.
- The PipelineRun `metadata.name` must contain the correct version for the
  branch immediately before `-on-push` or `-on-schedule`. For example,
  branch `rhoai-3.4` requires version `v3-4` in the name. EA (Early Access)
  versions like `v3-4-ea-2` are rejected because `v3-4` is not immediately
  followed by `-on-push`/`-on-schedule`.

**Note:** Branch and version checks only run when `--branch` is passed.
On `main`, only the repo annotation and `?rev={{revision}}` are validated.

### Check 5: CEL Self-Reference (`test_cel_self_reference`)

**Applies to:** push, scheduled only (when CEL expression filters `.tekton` paths)
**Severity:** Error

When a push/scheduled PipelineRun's CEL expression filters on `.tekton/**` paths
(e.g., `!".tekton/**".pathChanged()`), it must also include a
self-reference so the pipeline triggers when its own definition changes.

Expected pattern in the CEL expression:
```
".tekton/<filename>.yaml".pathChanged()
```

Where `<filename>` matches the actual YAML filename. If the CEL expression
does not reference `.tekton` paths at all, this check passes (nothing
to validate).

### Check 6: Quay Repo Existence (`test_quay_repo_existence`)

**Applies to:** All types
**Severity:** Error (repo not found) or Skip (no credentials)

Validates the Quay repository referenced in the `output-image` parameter
actually exists. Authentication uses `QUAY_RHOAI_READONLY_BOT_AUTH`
(base64-encoded `username:password`). The test exchanges this for a
no-scope bearer token via `GET /v2/auth?service=quay.io`, then fetches
the full list of accessible repos via `GET /v2/_catalog` (paginated,
cached once per session). Each PipelineRun's output-image repo is
checked against this cached list.

If `QUAY_RHOAI_READONLY_BOT_AUTH` is not set, this check is skipped.
Catalog fetch failures produce a warning.

### Check 7: Quay Naming Convention (`test_quay_naming`)

**Applies to:** All types
**Severity:** Error or Warning

Validates the `output-image` parameter follows naming conventions:

- **pull_request:** Must use `quay.io/rhoai/pull-request-pipelines:<tag>`
  and the tag must include `{{revision}}`.
- **push/scheduled:** Must be under `quay.io/rhoai/` and must NOT use the
  `pull-request-pipelines` repo. Tag typically includes `{{target_branch}}`
  (warning if missing).

### Check 8: Dockerfile Context Path (`test_dockerfile_path`)

**Applies to:** Container image builds only (skipped for Helm chart builds)
**Severity:** Error or Warning

Validates the Dockerfile specified in the `dockerfile` parameter exists in
the component's GitHub repository. The component repo is extracted from the
`build.appstudio.openshift.io/repo` annotation.

Helm chart builds (detected by `pipelineRef` pointing to
`pipelines/helm-chart-build.yaml`) are skipped — they do not use
Dockerfiles.

Path resolution order:
1. `<path-context>/<dockerfile>` — dockerfile relative to the build
   context (preferred)
2. `<dockerfile>` — dockerfile relative to the repo root (fallback)

When `path-context` is not specified or is `.`, only option 2 is checked.

For release-branch PipelineRuns (`--branch` is set), the test checks
both the default branch and the specified branch.

When the Dockerfile is not found, the error message lists available
Dockerfiles in the target directory to help the user pick the correct one.

Works without `GITHUB_TOKEN` for public repos. Private or inaccessible
repos are skipped with a warning. Setting `GITHUB_TOKEN` enables access
to private repos and provides a higher API rate limit (5,000/hour vs
60/hour unauthenticated).

### Check 9: Prefetch Input Validation (`test_prefetch_input`)

**Applies to:** All types (when `prefetch-input` param is present)
**Severity:** Error

Validates the `prefetch-input` parameter is well-formed. Accepted formats:

- **JSON string:** A string value containing valid JSON — either a single
  object (`{"type": "gomod", "path": "."}`) or an array of objects
  (`[{"type": "gomod"}, {"type": "rpm"}]`).
- **YAML sub-object:** A native YAML mapping or sequence (parsed directly
  by the YAML loader, not as a string).

The check rejects empty strings, non-JSON strings, and JSON arrays
containing non-object elements.

## GitHub Actions Workflow

### Trigger

```yaml
on:
  pull_request:
    branches: ['main', 'rhoai-*']
    paths: ['pipelineruns/**', 'script/test_validate_pipelineruns.py',
            'script/conftest.py', '.github/workflows/validate-pipelineruns.yml']
  workflow_dispatch:
    inputs:
      branch:
        type: string
        description: 'Release branch to validate against (e.g. rhoai-3.4)'
```

Runs automatically on pull requests and can be triggered manually via
`workflow_dispatch` with an optional `branch` input.

**Note:** `pull_request` workflows from forks do not have access to
repository secrets. For fork PRs, the Quay existence check will be
skipped (no credentials) and Dockerfile path checks will only work for
public repos (no token for private repo access).

### Branch Detection

For PRs targeting release branches (e.g., `rhoai-3.4`), the workflow
passes `--branch <target>` to enable branch-specific checks (checks 4
and 5). PRs targeting `main` do not pass `--branch`.

### PR Comment on Failure

When validation fails on a pull request, the workflow posts (or updates)
a comment on the PR summarizing which checks failed and why. The comment
includes:

- **Failures grouped by check name** — each check name links to its
  definition in the test source file.
- **Affected files** — each file name links to the relevant line in the
  PR's commit blob on GitHub.
- **Error messages** — concise messages extracted from pytest output.
  Multi-line errors (e.g., `test_dockerfile_path` listing available
  Dockerfiles) are rendered in fenced code blocks.
- **YAML snippets** — a collapsible `<details>` block shows the
  relevant lines from the PipelineRun file with a `>` marker on the
  matching line.
- **Skipped tests** — a collapsible section at the bottom groups skipped
  tests by reason, listing the check and file for each.
- **Footer** — links to the full CI run logs, the commit SHA, and a
  UTC timestamp.

A hidden HTML marker (`<!-- pipelinerun-validation-comment -->`) is used
to identify the comment so subsequent runs update it in place rather
than creating duplicates. When validation passes after a previous
failure, the comment is automatically deleted.

The comment body is generated by pytest via `--validation-comment-file`
and posted using the `gh` CLI.

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `QUAY_RHOAI_READONLY_BOT_AUTH` | No | Base64-encoded `username:password` for Quay API. If unset, Quay repo existence checks are skipped. |
| `GITHUB_TOKEN` | No | GitHub API token. Enables access to private repos and higher rate limits. Dockerfile checks work without it for public repos. |

## CLI Usage

```bash
# Validate all PipelineRuns (main branch)
pytest script/test_validate_pipelineruns.py --pipelinerun-dir pipelineruns/

# Validate for a release branch
pytest script/test_validate_pipelineruns.py --pipelinerun-dir pipelineruns/ --branch rhoai-3.4

# Verbose output (show each test case)
pytest script/test_validate_pipelineruns.py --pipelinerun-dir pipelineruns/ -v

# Using uv (no virtual env needed)
uv run --with pyyaml --with pytest pytest script/test_validate_pipelineruns.py \
    --pipelinerun-dir pipelineruns/ --branch rhoai-3.4
```

Each PipelineRun YAML file is discovered automatically and each validation
check runs as a separate pytest test case. The Quay repo existence check
is skipped when `QUAY_RHOAI_READONLY_BOT_AUTH` is not set. Dockerfile
path checks work without `GITHUB_TOKEN` for public repos; private repos
are skipped with a warning.

## Adding a New Check

1. Add a `test_<check_name>(pipelinerun_file, ...)` function in
   `script/test_validate_pipelineruns.py`. Use `_load(pipelinerun_file)`
   to parse the YAML. Use `return` for inapplicable types (passes the
   test), `pytest.skip()` only for missing credentials, and
   `pytest.fail()` or `assert` for errors. Use `warnings.warn()` for
   non-fatal warnings.
2. Add any new session-scoped fixtures (e.g., API caches) if the check
   needs external data.
3. Update this document with the new check's description, applicability,
   and severity.
