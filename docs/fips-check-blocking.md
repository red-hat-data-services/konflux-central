# FIPS check blocking validation

Automated guardrail for [RHAI-15255](https://redhat.atlassian.net/browse/RHAI-15255)
and [RHAI-15578](https://redhat.atlassian.net/browse/RHAI-15578):
PipelineRuns and shared Tekton pipelines in konflux-central must not set
`fips-check-blocking` to `false` unless the file is listed in an approved
exceptions file on **main**.

## Why it matters

When `fips-check-blocking` is `false`, a FIPS payload check failure does not
fail the Konflux pipeline. That is only acceptable with an explicit, reviewed
exception.

## Files

| File | Purpose |
|------|---------|
| `script/check_fips_check_blocking.py` | Scanner and PR comment generator |
| `script/test_fips_check_blocking.py` | Unit tests |
| `fips-check-exceptions.yaml` | Approved exceptions (branch-scoped), merged only on main |
| `.github/CODEOWNERS` | Required reviewers for the exceptions file |
| `.github/workflows/validate-fips-check-blocking.yml` | Canonical workflow on **main** |
| `.github/workflows/post-fips-check-comment.yml` | Posts results to the PR |

## Workflow behavior

1. Runs on pull requests to `main` / `rhoai-*` (no path filters), so a required
   check always gets a reported result when this workflow is triggered.
2. Checks out the pull request head.
3. Loads `fips-check-exceptions.yaml` from **origin/main** (not from the PR branch).
4. Selects the exception list where `branch` matches the PR **base** branch
   (`github.base_ref`, e.g. `main` or `rhoai-3.6`).
5. **Incremental scan (default):** only evaluates `pipelineruns/**/.tekton/**`
   and top-level `pipelines/*.yaml` files **changed in the PR** (three-dot
   `git diff` against the PR base SHA; falls back to a two-dot tree diff or a
   full scan if history is incomplete). Unrelated paths (for example
   `early-gate/**`, docs, CODEOWNERS-only) pass without re-scanning the whole
   tree.
6. **Full scan** when the PR changes the allowlist, checker, tests, or this
   workflow (or when `--full-scan` / workflow_dispatch requests it).
7. Fails if a scanned file sets `fips-check-blocking` to `false`, or a shared
   pipeline sets the parameter **default** to `false`, unless that
   repo-relative path appears in the exception list.

## Release branches

Do **not** copy the full workflow. On each `rhoai-*` branch that needs the check,
keep only this stub as `.github/workflows/validate-fips-check-blocking.yml`:

```yaml
name: Validate FIPS Check Blocking
on:
  pull_request:
    branches:
      - 'rhoai-*'
  workflow_dispatch:
jobs:
  validate:
    uses: red-hat-data-services/konflux-central/.github/workflows/validate-fips-check-blocking.yml@main
    with:
      target_branch: ${{ github.base_ref }}
      base_sha: ${{ github.event.pull_request.base.sha }}
      head_sha: ${{ github.event.pull_request.head.sha }}
```

Alternatively, a GitHub ruleset **Require workflows to pass** pointing at this
file on **main** can replace per-branch stubs entirely.

When enabling the check on a release branch, **pre-seed** that branch’s section
on **main** by scanning the release branch for existing `fips-check-blocking:
false` settings (PipelineRun params and shared pipeline defaults). Without
pre-seeding, PRs that touch those files would fail CI.

## Requesting an exception

1. Clone [OCPEXCEPT-1](https://redhat.atlassian.net/browse/OCPEXCEPT-1) and
   [RHAI-16830](https://redhat.atlassian.net/browse/RHAI-16830), and obtain
   approval comments on **both** tickets.
2. Open a pull request to **main** that adds an entry under the correct `branch`
   in `fips-check-exceptions.yaml` (`file` path and a `references` YAML list of
   Jira keys). The requester opens the PR; a CODEOWNERS reviewer approves it.
3. Re-run CI after the exception is merged to main.

## Local run

```bash
git fetch origin main --depth=1
git show origin/main:fips-check-exceptions.yaml > /tmp/fips-check-exceptions.yaml

# Full scan
uv run --with pyyaml python script/check_fips_check_blocking.py \
  --repo-root . \
  --exceptions-file /tmp/fips-check-exceptions.yaml \
  --target-branch rhoai-3.6 \
  --full-scan

# Incremental (changed-files list)
uv run --with pyyaml python script/check_fips_check_blocking.py \
  --repo-root . \
  --exceptions-file /tmp/fips-check-exceptions.yaml \
  --target-branch main \
  --changed-files /tmp/changed-files.txt
```

## Related

- PipelineRun validation: [validate-pipelineruns.md](validate-pipelineruns.md)
- Jira: [RHAI-15255](https://redhat.atlassian.net/browse/RHAI-15255),
  [RHAI-15578](https://redhat.atlassian.net/browse/RHAI-15578)
