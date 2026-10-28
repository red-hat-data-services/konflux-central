# FIPS check blocking validation

Automated guardrail for [RHAI-15255](https://redhat.atlassian.net/browse/RHAI-15255):
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
| `.github/workflows/validate-fips-check-blocking.yml` | Runs on pull requests |
| `.github/workflows/post-fips-check-comment.yml` | Posts results to the PR |

## Workflow behavior

1. Check out the pull request head (pipelineruns and pipelines as proposed).
2. Load `fips-check-exceptions.yaml` from **origin/main** (not from the PR branch).
3. Select the exception list where `branch` matches the PR **base** branch
   (`github.base_ref`, e.g. `main` or `rhoai-3.6`).
4. Fail if any file sets `fips-check-blocking` to `false`, or any shared pipeline
   under `pipelines/` sets the parameter **default** to `false`, unless that
   repo-relative path appears in the exception list.

Release-branch PRs use the checker script from **main** (same pattern as
PipelineRun validation) so logic does not need to be backported to every branch.

## Requesting an exception

1. File a Jira explaining why FIPS blocking must be disabled.
2. Add an entry under the correct `branch` in `fips-check-exceptions.yaml` on
   **main** (path + Jira key). CODEOWNERS enforcement is planned separately.
3. Re-run CI after the exception is on main.

## Local run

```bash
git fetch origin main --depth=1
git show origin/main:fips-check-exceptions.yaml > /tmp/fips-check-exceptions.yaml

uv run --with pyyaml python script/check_fips_check_blocking.py \
  --repo-root . \
  --exceptions-file /tmp/fips-check-exceptions.yaml \
  --target-branch rhoai-3.6
```

## Related

- PipelineRun validation: [validate-pipelineruns.md](validate-pipelineruns.md)
- Jira: [RHAI-15255](https://redhat.atlassian.net/browse/RHAI-15255)
