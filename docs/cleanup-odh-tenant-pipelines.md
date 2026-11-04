# ODH tenant PipelineRun cleanup

This manually dispatched workflow creates deletion-only component release PRs.
It is separate from normal PipelineRun sync and release-PR generation; it does
not merge PRs, change KC release inputs, or dispatch builds/live sync.

## Prerequisites

The shared validator's main branch must contain the generic `is_pipeline_run`
matcher. Namespace policy belongs only to `tekton-templates/cleanup-odh-tenant`:
`delete_if('open-data-hub-tenant')` uses the existing validator deletion mechanism.
No event/name heuristics, parent matching, or generated-child configuration apply.

The workflow must be on KC's default branch before GitHub exposes manual dispatch.
Run a reviewed workflow ref: that ref supplies the script and policy. Repository
selection comes from the selected KC release's `pipelineruns/` directories, as
in existing sync, not from main's newer component inventory. Supported releases
are 2.25, 3.4, 3.5, 3.6, and 3.7 EA.1.

## Preview first

Run **Preview or publish ODH tenant cleanup PRs**, selecting one repository and
release. Leave `dry_run: true` (the default). `all` selects that release's managed
repositories with bounded concurrency; start with one repository first.

Preview does not obtain the publication App token, commit, push, or create PRs.
It changes only a disposable staging directory, running actual validator dry-run,
fix, independent non-fix validation, and an idempotence pass. The component
checkout stays unchanged. Artifacts contain the inventory, exact deletion patch,
base/policy/tool revisions, policy digest, and logs—not the staging checkout.

Local equivalent, using a clean component checkout and shared tooling:

```sh
uv run script/cleanup_odh_tenant_pipelines.py prepare \
  --target /path/to/component --tooling /path/to/devops-shared-resources \
  --policy tekton-templates/cleanup-odh-tenant \
  --release rhoai-3.6 --repository ai-gateway-controller \
  --report /tmp/new-cleanup-report
```

The report directory must be new and outside the component checkout. Neither
local preparation nor the workflow requires the historical temporary cleanup
worktrees or their manifests.

## Publication

Only after reviewing/authorizing the preview, dispatch with `dry_run: false`.
The workflow still generates a fresh verified preview first. It then obtains an
App token scoped to the selected repository and checks out the exact preview
base for publication. Publication fails if the remote release has advanced.
It verifies the receipt/patch, exact namespace inventory, deletion-only index,
and retained bytes/modes before committing with DCO sign-off and pushing a
feature branch. Each PR references [RHAI-19583](https://redhat.atlassian.net/browse/RHAI-19583)
and includes provenance. Updating Jira's PR field is not automated here.

No-change pairs produce no PR. Open cleanup PRs identified by the automation
marker are reported instead of overwritten. A prior PR on the same deterministic
policy/release branch blocks automatic resurrection, whether closed or merged;
review its disposition explicitly. Superseded manual PRs on other branches do
not block the automated replacement. There is no force-push or auto-merge.
Publication can start ordinary repository CI/Konflux builds automatically.

## Fail-closed behavior

All YAML documents are strictly parsed before any fix. Duplicate keys, malformed
trailing documents, mixed/bundled PipelineRun streams, untracked Tekton files,
symlinks, incomplete matcher coverage, or modifications outside the exact
namespace deletion set block the pair. Valid resource-only streams survive
unchanged. Omitted namespaces and other tenants are never inferred as targets.

Malformed retained downstream files also block preparation; namespace filtering
is not permission to ignore parsing failures. Error receipts name the offending
YAML file and retain component, policy, validator, and inventory revisions, even
when parsing fails before staging. Resolve malformed files separately rather
than mixing repairs into a deletion-only PR. Batch jobs retain individual failures
and do not count failed/skipped cases as successful coverage.

## Local tests

With pytest and the script's dependencies in an existing Python environment,
set `CLEANUP_TEST_TOOLING` to the shared tooling checkout and run
`script/test_cleanup_odh_tenant_pipelines.py` and
`script/test_cleanup_odh_tenant_publication.py`. This enables actual validator
integration tests alongside file-safety and publication-gate regressions. Tests
never publish remote PRs. No workflow-shape assertion tests are required.
