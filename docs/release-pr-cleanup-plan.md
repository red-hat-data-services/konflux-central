# Release PipelineRun cleanup plan

## Status and ownership

Tracking: [RHAI-19583](https://redhat.atlassian.net/browse/RHAI-19583), under
[RHAI-19180](https://redhat.atlassian.net/browse/RHAI-19180).

This plan records the approved local preparation and proposed publication of
one-time component cleanups. It is not authorization to publish component PRs,
merge PRs, dispatch live sync, or run builds. **RHOAI 3.6 cleanup is no longer
blocked on the GA date.** All releases follow the same review/publication gates.

## Removal criterion

Remove component-repository definitions explicitly declaring:

```yaml
metadata:
  namespace: open-data-hub-tenant
```

This includes push, pull-request, tag, E2E, early-gate, and group-test
PipelineRuns. Do not infer the execution namespace from an application name,
Dockerfile, registry path, or an `odh-` filename. Definitions targeting
`rhoai-tenant`, or omitting the namespace, require separate review.

The pinned inventory identified 339 files in 111 repository/release pairs. All
are PipelineRuns; no target file mixes ODH-tenant and other-tenant documents.
No matching KC source definitions were found in the audited snapshots.
These counts are candidate-state inventory, not current live-branch guarantees.

| Component release branch | Cleanup branches / proposed PRs | Files |
| --- | ---: | ---: |
| `rhoai-2.25` | 14 | 19 |
| `rhoai-3.4` | 28 | 118 |
| `rhoai-3.5` | 21 | 72 |
| `rhoai-3.6` | 22 | 62 |
| `rhoai-3.7-ea.1` | 26 | 68 |
| **Total** | **111** | **339** |

## Prepared local changes

- Each affected repository/release has an isolated feature branch/worktree with
  only the exact approved file deletions staged. No cleanup commits or pushes
  have been made for this batch.
- Every deletion was rechecked using strict YAML parsing and the exact namespace.
  Git comparisons verified all retained tracked files are unchanged.
- Seven now-obsolete ownership-policy entries were removed from the local KC
  implementation. Other ownership rules remain unchanged.
- There is **no persistent retirement/deletion mechanism** in sync preparation.
  These deletions belong in one-time component PRs, never hidden repair logic.

Local worktrees use `chore/remove-odh-tenant-<release>-<audit-id>` branches.
Do not modify unrelated worktrees, amend existing commits, or push to main.

## Publication sequence and gates

1. **Refresh each component base.** The inventory is pinned, not a rolling scan.
   Compare current release HEAD with the recorded base and account for merged
   cleanups. Reconstruct the minimal patch on a fresh feature branch if needed;
   do not force-push or overwrite other work.
2. **Revalidate the refreshed patch.** Require exact-tenant deletions only, no
   additions/modifications, byte-identical retained files, and a clean whitespace
   check. Keep existing namespace-independent defects visible rather than
   repairing them inside this cleanup batch.
3. **Publish only after separate approval.** Use one deletion-only PR per
   repository/release pair; link the cleanup subtask in each description and
   attach each PR to Jira's Git Pull Request field. Include the file list,
   namespace rationale, and validation evidence. Refreshing bases can reduce the
   111-PR count if equivalent removals have already landed.
4. **Review and coordinate merges separately.** Do not dispatch builds or sync
   as a side effect of publication. Existing CI may run automatically on PRs.
5. **Re-audit merged component states.** Candidate-state success does not certify
   the unchanged release branches. Preserve source/component revisions, errors,
   and all fix/non-fix/idempotence reports.
6. **Roll out generation separately.** Land shared tooling before KC policy;
   coordinate the removed ownership entries with component cleanup so unchanged
   components are not enabled prematurely. Propagate the updated sync workflow
   to each intended KC release and inspect a separately authorized dry run before
   permitting normal component pushes. Main landing alone does not update release
   workflows. Older-release ownership/cleanup blockers must be resolved first.

## Existing PRs and implementation work

- [Shared tooling #41](https://github.com/red-hat-data-services/devops-shared-resources/pull/41)
  and [KC integration #4070](https://github.com/red-hat-data-services/konflux-central/pull/4070)
  carry generation, sync integration, bounded naming, compatibility fixes,
  remaining ownership policy, and documentation. They do not carry component
  file deletions.
- CLI cleanup [#565](https://github.com/red-hat-data-services/odh-cli/pull/565)
  and [#566](https://github.com/red-hat-data-services/odh-cli/pull/566) remain open
  at plan preparation; they are separate from this exact-namespace batch.
- OpenVINO component cleanups [#769](https://github.com/red-hat-data-services/openvino_model_server/pull/769)
  and [#770](https://github.com/red-hat-data-services/openvino_model_server/pull/770),
  plus KC source cleanups [#4110](https://github.com/red-hat-data-services/konflux-central/pull/4110)
  and [#4111](https://github.com/red-hat-data-services/konflux-central/pull/4111),
  are merged. Their remaining ODH-tenant push-file removals need new component PRs.
- Notebook baseline cleanup [#3331](https://github.com/red-hat-data-services/notebooks/pull/3331)
  is merged. Timeout-only [#3330](https://github.com/red-hat-data-services/notebooks/pull/3330)
  was closed as superseded. The new notebook tenant cleanup targets 3.7 EA.

PR state is a preparation-time observation; recheck before publication.

## Candidate audit and remaining blockers

After applying the namespace cleanups in disposable copies, actual
`validate-tekton --fix`, independent non-fix validation, and idempotence checks
produced:

| Release | Validated repositories | Blocked repositories | Validated push/PR pairs |
| --- | ---: | ---: | ---: |
| `rhoai-2.25` | 41 | 0 | 73 |
| `rhoai-3.4` | 47 | 3 | 94 |
| `rhoai-3.5` | 53 | 3 | 113 |
| `rhoai-3.6` | 67 | 0 | 146 |
| `rhoai-3.7-ea.1` | 67 | 0 | 146 |

The successful candidates contained no remaining explicit ODH-tenant resources.
The newer-release inputs also include the earlier CLI/OpenVINO/notebook cleanup
candidate revisions. Therefore this is not a live-state rollout approval.

Six repository/release blockers remain outside the approved tenant criterion:

| Repository | Release | Required decision or follow-up |
| --- | --- | --- |
| `odh-cli` | 3.4, 3.5 | Review one-time removal of duplicate `odh-cli.yaml`; retain standard delivery PR. |
| `pipelines-components` | 3.4 | Choose AutoML/AutoRAG delivery filenames; coordinate component and KC source cleanup if retaining unversioned definitions. |
| `distributed-workloads` | 3.4 | Review minimal repair of identical duplicate `name: build-images` keys in two retained ROCm push files; re-audit for defects hidden by parsing failure. |
| `distributed-workloads` | 3.5 | Decide ownership of four unmatched downstream legacy Ray/training PRs; do not alias them to unrelated current parents. |
| `ai-gateway-payload-processing` | 3.5 | Review separate downstream E2E PR ownership without a delivery push parent. |

These follow-ups are not silently included in the tenant-deletion patches and
need their own approved changes. Scope any preservation decisions to exact
reviewed identities and releases; preservation does not certify existing CI.

## Evidence and reproducibility

See [release-pr-sync-audit.md](release-pr-sync-audit.md) for source revisions and
validation boundaries. Local evidence (temporary, not durable CI storage):

- `/tmp/odh-tenant-cleanup.iusuugji/manifest.json`: exact base revisions, branches,
  worktrees, deletion lists, and scheduling flags for all 111 patches.
- `/tmp/release-pr-review/five-release-tenant-inventory.json`: namespace inventory.
- `/tmp/five-release-tenant-cleanup-audit.a2mxqv9g/`: per-repository reports/results.

Before component publication, retain exact filename/base manifests and raw audit
reports in durable review artifacts. Do not rely on another machine's `/tmp`.

## Repository/release matrix

Counts below are exact files to remove per candidate. A dash means no files in
this tenant-removal batch; it is not a statement about repository support.

| Repository | 2.25 | 3.4 | 3.5 | 3.6 | 3.7 EA.1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ai-gateway-controller` | - | - | - | 1 | - |
| `ai-gateway-operator` | - | - | 1 | 1 | 1 |
| `ai-gateway-payload-processing` | - | 3 | 5 | - | - |
| `argo-workflows` | - | 4 | - | - | - |
| `batch-gateway` | - | 13 | 6 | - | - |
| `codeflare-operator` | 1 | - | - | - | - |
| `data-connect-hub` | - | - | - | 4 | 4 |
| `data-science-pipelines` | - | 10 | - | - | - |
| `data-science-pipelines-operator` | - | 1 | - | - | - |
| `distributed-workloads` | - | 20 | 10 | 3 | 3 |
| `eval-hub-contrib` | - | - | - | 4 | 2 |
| `feast` | 2 | 5 | - | 1 | 1 |
| `kserve` | 4 | 10 | 15 | 12 | 12 |
| `kube-rbac-proxy` | - | - | 1 | - | - |
| `kubeflow` | 2 | 2 | 2 | - | - |
| `kuberay` | 1 | 1 | 1 | 1 | 1 |
| `llm-d-async` | - | - | 1 | - | - |
| `llm-d-inference-scheduler` | 1 | - | - | - | - |
| `llm-d-kv-cache` | - | 1 | - | - | 1 |
| `llm-d-latency-predictor` | - | - | 3 | - | - |
| `llm-d-router` | - | - | - | 2 | 2 |
| `llm-d-routing-sidecar` | 1 | - | - | - | - |
| `lm-evaluation-harness` | - | 2 | 2 | - | - |
| `mcp-lifecycle-module-operator` | - | - | - | - | 4 |
| `mcp-lifecycle-operator` | - | - | - | 3 | 1 |
| `ml-metadata` | - | 1 | - | - | - |
| `mlflow` | - | 1 | 1 | - | - |
| `mlflow-operator` | - | 3 | - | - | - |
| `model-metadata-collection` | - | 2 | - | - | - |
| `model-registry` | - | 2 | - | - | - |
| `model-registry-operator` | - | 1 | - | - | - |
| `modelexpress` | - | - | - | 4 | 2 |
| `modelmesh` | 1 | - | - | - | - |
| `modelmesh-runtime-adapter` | 1 | - | - | - | - |
| `modelmesh-serving` | 1 | - | - | - | - |
| `models-as-a-service` | - | 4 | 6 | - | - |
| `NeMo-Guardrails` | - | 1 | 2 | - | - |
| `notebooks` | - | - | - | - | 4 |
| `odh-dashboard` | - | 14 | 3 | 4 | 4 |
| `odh-model-controller` | 1 | 1 | 3 | - | - |
| `ogx-distribution` | - | 2 | 1 | - | - |
| `ogx-k8s-operator` | 1 | 2 | - | 3 | 3 |
| `openshell` | - | - | - | 9 | 9 |
| `openshell-dashboard` | - | - | - | - | 1 |
| `openvino_model_server` | - | - | 2 | 1 | 1 |
| `pipelines-components` | - | 6 | - | - | - |
| `praxis-ai-grid-operator` | - | - | - | 1 | - |
| `praxis-extproc` | - | - | - | 2 | - |
| `ray-module-operator` | - | - | - | - | 1 |
| `rest-proxy` | 1 | - | - | - | - |
| `rhai-db-operator` | - | - | - | - | 3 |
| `rhaii-cluster-validation` | - | - | 4 | - | - |
| `rhods-operator` | - | - | 2 | 2 | 2 |
| `spark-operator` | - | 3 | 1 | - | 2 |
| `trainer` | - | 2 | - | - | - |
| `trainer-operator` | - | - | - | 1 | 1 |
| `training-operator` | 1 | 1 | - | - | - |
| `trustyai-service` | - | - | - | 1 | 1 |
| `workbenches` | - | - | - | 1 | 1 |
| `workbenches-operator` | - | - | - | 1 | 1 |
| **Files** | **19** | **118** | **72** | **62** | **68** |
