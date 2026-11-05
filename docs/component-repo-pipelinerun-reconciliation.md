# Component Repo PipelineRun Reconciliation

Tracking: [RHAI-19583](https://redhat.atlassian.net/browse/RHAI-19583), under
[RHAI-19180](https://redhat.atlassian.net/browse/RHAI-19180).

Propose reviewed corrections to component-repository PipelineRuns using centrally
reviewed templates. ODH retirement is one selectable template set, not a separate
reconciliation engine. The workflow is manually dispatched, with preview enabled
by default and no automatic merges.

Each job checks out shared tooling from
[`main`](https://github.com/red-hat-data-services/devops-shared-resources/tree/main).
Shared fixes and improvements are adopted automatically on later checkouts;
a shared regression can also affect consumers without a separate pin-update PR.
No dirty-source production override is used. Workflow dispatch and component
publication still require separate authorization.

## Ownership boundary

The workflow invokes the end-to-end `validate-tekton` command from
[shared resources](https://github.com/red-hat-data-services/devops-shared-resources/blob/main/scripts/validate-tekton).
There is **no KC component reconciliation Python runner, loader, receipt adapter,
or publisher module**.

Shared code owns:

- Safe inventory enumeration/membership checks and batch-publication gates.
- Tekton discovery, strict YAML/resource classification, template configuration
  and digest checks, and actual rendering.
- Initial validation, preview, fix, non-fix validation, and idempotence in an
  isolated checkout; scope/path/kind/mode/index and retained-byte checks.
- Plans, useful reports and GitHub outputs, exact trusted receipt verification,
  bounded replay, and optional validated-change publication.
- Runtime source checks, source provenance, and generic PR changed-file,
  validation, and provenance summaries.

KC supplies policy and choices only:

- Five allowed releases: `rhoai-2.25`, `rhoai-3.4`, `rhoai-3.5`, `rhoai-3.6`, and
  `rhoai-3.7-ea.1`; target organization `red-hat-data-services`.
- The selected KC release's `pipelineruns` inventory and the selected reviewed
  template set under `tekton-templates`. `template_set` is required; `default`
  explicitly selects the template root. Names cannot be arbitrary paths.
- Preview/batch inputs, fleet concurrency, narrowly scoped App token orchestration,
  PR identity, static [PR notes](component-repo-pipelinerun-pr-body.md), and an
  optional validated Jira tracking link.

Each reconciliation job uses the same shared checkout for validation, planning
and publication, without refreshing it between phases. Shared checks its clean
runtime revision and records the resolved commit SHA in reports and publication
provenance; replay must match that identity. Inventory and reconciliation jobs
may resolve different commits if shared `main` advances during a workflow run.
Artifacts do not select executable code. Actual shared runtime, policy/template
source, and inventory provenance remain distinct even when some revisions happen
to be equal. The common overlay-containment checks prevent target labels or
name-derived prefixes from selecting templates outside the trusted root.

## Reconcile and optionally publish

Inventory uses shared `--inventory-root`, `--select-repository`, and
`--allowed-branches`. Shared returns the matrix and inventory revision. Selecting
`all` for publication requires **both** `dry_run: false` and
`allow_batch_publication: true`, checked before fanout. Both booleans have strict
semantics; batch confirmation cannot itself disable preview.

For each selected repository, the workflow invokes shared reconciliation:

```bash
uv run --project shared --frozen shared/scripts/validate-tekton \
  --reconcile --path target \
  --config-dir policy/tekton-templates/cleanup-odh-tenant \
  --tekton-layout flat --allowed-operations M,D \
  --branch rhoai-3.6 --allowed-branches "$ALLOWED_BRANCHES" \
  --inventory-revision "$INVENTORY_REVISION" --source-sha "$GITHUB_SHA" \
  --report-dir "$RUNNER_TEMP/enforcement-report" \
  --github-output "$GITHUB_OUTPUT" --json
```

Only existing-file modifications and explicit template-driven deletions are
allowed. No additions, renames, resource-kind changes, or mode changes. Malformed
or duplicate-key YAML, bundled PipelineRun streams, unsafe paths/symlinks,
unselected mutations, incomplete fixes, and non-idempotence fail closed.
Unmatched resources and retained bytes/modes remain unchanged. Valid resource-only
streams and no-PipelineRun repositories are no-ops. Nested KC migration policies,
external parent references, and new-file generation are outside this profile.

Shared emits `status`, `base`, and `policy_digest_short` as GitHub step outputs.
The workflow does not parse or modify receipts. Preview obtains no publication
token. Only a ready plan with `dry_run: false` obtains a one-repository App token
with explicit contents/write and pull-requests/write permissions, then checks out
the exact preview base into `publish-target`.

The **same shared command** resumes the report, with trusted configuration and
branch inputs repeated, plus ordinary PR metadata:

```bash
uv run --project shared --frozen --no-sync --offline \
  shared/scripts/validate-tekton \
  --resume-report "$RUNNER_TEMP/enforcement-report" --publish \
  --path publish-target --config-dir "$CONFIG_DIR" \
  --tekton-layout flat --allowed-operations M,D \
  --branch "$RELEASE" --allowed-branches "$ALLOWED_BRANCHES" \
  --inventory-revision "$INVENTORY_REVISION" --source-sha "$GITHUB_SHA" \
  --repository "red-hat-data-services/$REPOSITORY" \
  --head "automation/enforce-pipelineruns-${RELEASE}-${TEMPLATE_SET}-${POLICY_DIGEST_SHORT}" \
  --marker "<!-- template-set:${TEMPLATE_SET} -->" \
  --title "chore: reconcile component repo PipelineRuns on ${RELEASE}" \
  --body-file policy/docs/component-repo-pipelinerun-pr-body.md \
  --tracking-url "$TRACKING_URL" --lifecycle create-or-report --json
```

`--resume-report` without `--publish` verifies in a disposable checkout and leaves
the caller unchanged. Explicit publication verifies source/configuration/base,
exact candidates and receipt authorization before delegating to shared publication.
No template rendering or local policy callbacks are introduced under the token.
Existing open policy PRs are reported, not overwritten; prior PRs on the exact
head require explicit disposition. Git pushes are non-force.

## Policy, identity, and reports

The included `cleanup-odh-tenant` template removes only PipelineRuns with the exact
explicit execution namespace `open-data-hub-tenant`. Names, event annotations,
images, and labels are not namespace evidence. Other namespaces, omitted/null
namespace fields, and non-PipelineRun resources remain unchanged. Cleanup for
3.6 is not GA-gated.

Preserve these operational identities:

- Head: `automation/enforce-pipelineruns-{release}-{template_set}-{digest[:12]}`.
- Policy marker: `<!-- template-set:{template_set} -->`.
- Legacy body marker: `<!-- pipelinerun-template-enforcement -->`.
- Title: `chore: reconcile component repo PipelineRuns on {release}`.
- Lifecycle: `create-or-report`.
- Concurrency and artifact prefix: `plr-enforce-{release}-{repository}`.

Retain useful shared results, phase JSON/logs, a diagnostic patch, and the final
publication receipt. Internal bundle/request storage belongs to shared code.
Obsolete local inventory/request/PR sidecars are not compatibility requirements.
Uploads contain selected root diagnostic files and bounded `phases/*.json` and
`phases/*.log`, never staging/checkout or internal bundle trees. Publication reuses
the environment prepared during planning with uv `--no-sync --offline`; that
avoids dependency work after token acquisition, not a new isolation boundary.
Generic
coordinator/publication tests live in shared resources; KC retains actual included
template-semantic tests, not mocked workflow structure or another coordinator.

## Trust and rollout limits

Reviewed KC/shared code and templates are trusted; target YAML is data. Planning
and publication remain in the same job. Trusted plan/receipt checks authorize exact
replay; hashes provide integrity, not independent policy authorization. This does
not claim protection from a compromised planner or fresh-runner independent
rederivation of arbitrary template transformations. PR review is the approval
boundary; the automation never automatically merges corrections.

This differs from [Central PipelineRun Template Alignment (#4171)](https://github.com/red-hat-data-services/konflux-central/pull/4171),
which targets centrally managed KC release definitions. Future repository-level
PipelineRun Policy Validation, ownership transfer, and central-sync retirement are
not implemented here. Central sync can still overwrite direct component fixes;
coordinate ownership before operational rollout.

No schedule, live fleet refresh, component publication, workflow dispatch, merge,
Jira mutation, or live sync/build execution is authorized by this implementation.
Component publication may trigger normal CI/Konflux when separately approved.
