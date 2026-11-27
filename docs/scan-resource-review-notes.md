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

## Push routing

Container-specific matchers route supported push/PR/scheduled files to their
resource templates. `push.yaml.j2` copies `container-scheduled.yaml.j2` resources
and reconciles CEL to `konflux_cluster == "stone-prod-p02"`. Main container PRs
also receive whole-expression P02 routing and their configured platform policy;
release-branch PRs are excluded. Container scheduled CEL remains unchanged.
Chart/FBC push and scheduled templates own git-auth/P02 routing only, never RPM
scan overrides. P02 Repository parameters must supply `konflux_cluster`.

There is no selection sidecar. The default template is a no-op: unknown paths,
non-scan inline Pipelines and unsupported family events stay unchanged. Successful
inventory/preview does not mean every discovered file received resource changes.

## Scan-resource review before merging

Resource changes are limited to the scan step's CPU/memory fields and removal
of its conflicting task-level resource block. The git-auth workspace convention
and eligible push CEL routing are also enforced. Other tasks' budgets remain unchanged.
For Git references to `pipelines/multi-arch-container-build.yaml` in
`https://github.com/red-hat-data-services/konflux-central.git`, the policy assumes
that the scan task exists regardless of revision, including CEL-selected
revisions. Revision expressions are preserved, not evaluated; this is a path
contract, not verification of every possible runtime Pipeline. Recognized
single/family references must satisfy the guard's repository/local-definition
compatibility assertions or planning fails closed. No sources are fetched or
guessed. Review changed paths, no-op inputs and branch exclusions; successful
preview is not complete branch alignment or cluster verification.

Verify the admitted scan container has **500m CPU and 1Gi memory for both requests
and limits**, including bundled defaults and admission behavior. Tekton merges
resource keys, so both limits are explicit rather than relying on omission to
clear bundled limits. Review scan outcome/duration, OOM/throttling behavior,
scheduling, and quota impact. The **500m/1Gi baseline is provisional**.

Do not auto-merge. No manual Konflux rerun or deployment is requested; normal PR CI may run.

Related: [RHAI-19093](https://redhat.atlassian.net/browse/RHAI-19093).
