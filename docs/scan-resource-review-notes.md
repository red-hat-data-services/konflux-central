## Proposed updates

Align the `rpms-signature-scan` step resources in KC-managed release PipelineRuns:

- CPU request and limit: both **500m**.
- Memory request and limit: both **1Gi**.
- Remove the matching scan task's task-level `computeResources` override before
  configuring step resources; Tekton forbids combining those override scopes.

Existing larger reservations are intentionally standardized down to this
baseline. Deep merging preserves unrelated task resources, settings, other step
overrides, and timeouts. The generated context identifies the workflow
run, template directory and exact source revision, and lists the modified files.

## Scan-resource review before merging

Only the scan step's CPU/memory fields and removal of its conflicting task-level
resource block are automated. Other tasks' resource budgets remain unchanged.
For Git references to `pipelines/multi-arch-container-build.yaml` in
`https://github.com/red-hat-data-services/konflux-central.git`, the policy assumes
that the scan task exists regardless of revision, including CEL-selected
revisions. Revision expressions are preserved, not evaluated; this is a path
contract, not verification of every possible runtime Pipeline. Other unresolved
references are not fetched or guessed. Review the generated summary's deferred
paths and reasons; a successful preview is not complete branch alignment.

Verify the admitted scan container has **500m CPU and 1Gi memory for both requests
and limits**, including bundled defaults and admission behavior. Tekton merges
resource keys, so both limits are explicit rather than relying on omission to
clear bundled limits. Review scan outcome/duration, OOM/throttling behavior,
scheduling, and quota impact. The **500m/1Gi baseline is provisional**.

Do not auto-merge. No manual Konflux rerun or deployment is requested; normal PR CI may run.

Related: [RHAI-19093](https://redhat.atlassian.net/browse/RHAI-19093).
