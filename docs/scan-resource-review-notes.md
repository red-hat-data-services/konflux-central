## Proposed updates

Align the `rpms-signature-scan` step resources in KC-managed release PipelineRuns:

- CPU request: `max(existing, 250m)`.
- Memory request and limit: both `max(existing request, existing limit, 512Mi)`.
- CPU limit: removed from the scan step.

Larger existing reservations are preserved. Unrelated task resources, fields,
and timeouts are not policy targets. The generated context identifies the workflow
run, template directory and exact source revision, and lists the modified files.

## Scan-resource review before merging

Only scan-step CPU/memory policy is automated. Task-level budgets remain manual.
For Git references to `pipelines/multi-arch-container-build.yaml` in
`https://github.com/red-hat-data-services/konflux-central.git`, the policy assumes
that the scan task exists regardless of revision, including CEL-selected
revisions. Revision expressions are preserved, not evaluated; this is a path
contract, not verification of every possible runtime Pipeline. Other unresolved
references are not fetched or guessed. Review the generated summary's deferred
paths and reasons; a successful preview is not complete branch alignment.

Verify the admitted scan pod has the intended memory/CPU requests and **no CPU ceiling**, including bundled defaults and admission behavior. Removing a field from YAML does not prove the admitted limit is absent. Review scan outcome/duration, scheduling, and quota impact. The **512Mi baseline is provisional**.

Do not auto-merge. No manual Konflux rerun or deployment is requested; normal PR CI may run.

Related: [RHAI-19093](https://redhat.atlassian.net/browse/RHAI-19093).
