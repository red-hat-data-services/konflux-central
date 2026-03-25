# PipelineRun Validation

Automated validation checks for PipelineRun YAML files in the
konflux-central repository. Implemented per
[RHOAIENG-55175](https://redhat.atlassian.net/browse/RHOAIENG-55175).

## Overview

A GitHub Actions workflow runs a Python validation script against **all**
PipelineRun files on every pull request. The goal is to catch configuration
errors before they are merged and synced to component repositories.

### Files

| File | Purpose |
|------|---------|
| `script/validate-pipelineruns.py` | Python validation script (all check logic) |
| `.github/workflows/validate-pipelineruns.yml` | GitHub Actions workflow definition |
| `docs/validate-pipelineruns.md` | This documentation |

## PipelineRun Types

The script auto-detects three PipelineRun types from annotations and names:

| Type | Detection | Example name suffix |
|------|-----------|-------------------|
| **pull_request** | `pipelinesascode.tekton.dev/on-event` contains `pull_request` | `-on-pull-request-XXXXX` |
| **push** | CEL expression contains `"push"` and name does NOT contain `-on-schedule` | `-on-push` |
| **scheduled** | CEL expression contains `"push"` and name contains `-on-schedule` | `-on-schedule` |

## Checks

### Check 1: YAML Linting (`yaml-lint`)

**Applies to:** All files
**Severity:** Error

Validates the file is parseable YAML containing a mapping (dict). Also
verifies `kind: PipelineRun`.

### Check 2: Name Convention (`name-convention`)

**Applies to:** All types
**Severity:** Error

Validates `metadata.name` follows the naming pattern:

- **push:** Must end with `-on-push`
- **pull_request:** Must contain `-on-pull-request`
- **scheduled:** Must end with `-on-schedule`

### Check 3: Name Consistency (`name-consistency`)

**Applies to:** push, scheduled only
**Severity:** Error

Validates `metadata.name` is consistent with the
`appstudio.openshift.io/component` label. The name (minus the `-on-push`
or `-on-schedule` suffix) should start with the component label value.

- Example: name `odh-dashboard-v3-4-on-push` → component label
  `odh-dashboard-v3-4`

Pull request PipelineRuns are excluded from this check because their
component labels use abbreviated names that don't consistently match the
PipelineRun name.

### Check 4: Branch and Repo Targeting (`branch-repo-targeting`)

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

**Note:** This check only runs when `--branch` is passed to the script.
On `main`, no branch validation is performed for push PipelineRuns.

### Check 5: CEL Self-Reference (`cel-self-reference`)

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
does not reference `.tekton` paths at all, this check is skipped.

### Check 6: Quay Repo Existence (`quay-repo-existence`)

**Applies to:** All types
**Severity:** Error (404) or Warning (auth/network issues)

Validates the Quay repository referenced in the `output-image` parameter
actually exists by calling the Quay API:
```
GET https://quay.io/api/v1/repository/<namespace>/<repo>
```

Authentication uses `QUAY_RHOAI_READONLY_BOT_AUTH` (base64-encoded
`username:password`). The script exchanges this credential for a scoped
bearer token via the Docker v2 token endpoint (`quay.io/v2/auth`), then
checks each repo via `quay.io/v2/{repo}/tags/list`. Results are cached
per repo. If the credential is not set, this check is skipped (with an
upfront warning). Auth/network errors are reported as warnings, not
errors.

### Check 7: Quay Naming Convention (`quay-naming`)

**Applies to:** All types
**Severity:** Error or Warning

Validates the `output-image` parameter follows naming conventions:

- **pull_request:** Must use `quay.io/rhoai/pull-request-pipelines:<tag>`
  and the tag must include `{{revision}}`.
- **push/scheduled:** Must be under `quay.io/rhoai/` and must NOT use the
  `pull-request-pipelines` repo. Tag typically includes `{{target_branch}}`
  (warning if missing).

### Check 8: Dockerfile Context Path (`dockerfile-path`)

**Applies to:** All types
**Severity:** Error or Warning

Validates the Dockerfile specified in the `dockerfile` parameter exists in
the component's GitHub repository. The component repo is extracted from the
`build.appstudio.openshift.io/repo` annotation.

Path resolution order:
1. `<path-context>/<dockerfile>` — dockerfile relative to the build
   context (preferred)
2. `<dockerfile>` — dockerfile relative to the repo root (fallback)

When `path-context` is not specified or is `.`, only option 2 is checked.

For release-branch PipelineRuns (`--branch` is set), the script checks
both the default branch and the specified branch.

When the Dockerfile is not found, the error message lists available
Dockerfiles in the target directory to help the user pick the correct one.

Requires `GITHUB_TOKEN` for GitHub API access. If the token is not set,
this check is skipped (with an upfront warning).

### Check 9: Prefetch Input Validation (`prefetch-input`)

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
    paths: ['pipelineruns/**', 'script/validate-pipelineruns.py',
            '.github/workflows/validate-pipelineruns.yml']
```

Uses `pull_request` to run validation on the PR's merged code. The
workflow checks out the PR branch directly with a single checkout step.

**Note:** `pull_request` workflows from forks do not have access to
repository secrets. The `QUAY_RHOAI_READONLY_BOT_AUTH` and `GITHUB_TOKEN`
secrets will only be available for PRs from branches within the same
repository. For fork PRs, the Quay existence check and Dockerfile path
check will be skipped (with an upfront warning in the log).

### Branch Detection

For PRs targeting release branches (e.g., `rhoai-3.4`), the workflow
passes `--branch <target>` to enable branch-specific checks (checks 4
and 5). PRs targeting `main` do not pass `--branch`.

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `QUAY_RHOAI_READONLY_BOT_AUTH` | Yes | Base64-encoded `username:password` for Quay API |
| `GITHUB_TOKEN` | Yes | GitHub API access for Dockerfile path checks |

## CLI Usage

```bash
# Validate all PipelineRuns (main branch)
python script/validate-pipelineruns.py --pipelinerun-dir pipelineruns/

# Validate for a release branch
python script/validate-pipelineruns.py --pipelinerun-dir pipelineruns/ --branch rhoai-3.4

# JSON output
python script/validate-pipelineruns.py --pipelinerun-dir pipelineruns/ --output json

# GitHub Actions output (annotations + step summary)
python script/validate-pipelineruns.py --pipelinerun-dir pipelineruns/ --output github-actions
```

## Adding a New Check

1. Add a check function in `script/validate-pipelineruns.py` following the
   pattern of existing checks (accept `data`, `result`, and relevant
   context; call `result.error()` or `result.warn()`).
2. Call it from `validate_pipelinerun()`, gating on `pr_type` if it only
   applies to certain PipelineRun types. Add a `result.passed("<check-name>")`
   call after it so the check appears in the all-green summary.
3. Update this document with the new check's description, applicability,
   and severity.
