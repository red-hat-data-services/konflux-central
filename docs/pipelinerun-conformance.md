# PipelineRun template alignment

Desired policy remains in `tekton-templates/pipelineruns/config.yaml` and
`common.yaml.j2`, separate from parent-based PR migration templates. Push, PR,
and scheduled PipelineRuns use this template set with nested layout and no
parent inheritance.

## Ownership

The shared `reconcile-tekton` command owns inventory/discovery, safe YAML and
alias detachment, readonly branch-local context, classification, template
configuration/overlays, rendering/serialization, disposable stages, all five
phases, scope/mode checks, diagnostic reports and optional PR publication.
There is no consumer reconciliation runner, artifact transport, source loader,
publication adapter or report parser.

The only local production Python is `script/scan_resource_policy.py`:

- `CONTEXT_PATHS` fixes the two permitted branch-local Pipeline documents.
- The literal CPU/memory baseline lives in `common.yaml.j2`; the module reads it
  once at load time so future template edits also update the candidate guard.
- `decision(data, context)` selects the scan task (including `finally`) and
  defers unsupported references. Resource values are not target-derived.
- `check(original, candidate, context)` permits only scan task-budget removal and
  the four owned step resource fields, requires the template baseline, and
  rejects mixed task/step resource scopes. It returns `True` or raises.

Callbacks receive detached data and parsed context (`branch`, `documents`), not
filesystem roots, stage/phase/report handles or commands. They do not perform IO,
network resolution, rendering, serialization or publication. Shared code owns
context containment and rejects unsafe YAML before invoking policy.

## Policy and limits

Standardize the `rpms-signature-scan` step to CPU request/limit **500m** and
memory request/limit **1Gi**, even when existing reservations are larger. The
template explicitly deletes `computeResources` on the matching task override
before setting step resources: Tekton does not permit both override scopes.
Ordinary deep merging preserves other tasks, settings, step overrides and
per-task timeouts. Duplicate scan task/step identities remain errors.

For Git references to exactly
`pipelines/multi-arch-container-build.yaml` in
`https://github.com/red-hat-data-services/konflux-central.git`, task existence is
an explicit policy contract: enforce scan resources regardless of the revision,
including CEL-selected revisions. The revision expression is preserved, not
evaluated, fetched or verified against every possible runtime Pipeline.

Inline Pipelines and the single-arch `pipelines/container-build.yaml` reference
retain their existing scan-task checks. Single-arch references must resolve from
allowlisted branch-local context; unsupported external/dynamic/missing references
remain unresolved. Other repositories and Pipeline paths do not inherit the
multi-arch contract.

The 500m/1Gi baseline is provisional and intentionally lowers larger existing
budgets. Both CPU and memory limits are explicit because Tekton merges step
resource keys with the bundled Task defaults; omission does not clear a bundled
limit. Before rollout, validate exact bundles, admitted resources, scan
outcome/duration, OOM/throttling behavior and scheduling/quota impact.

## Trust and source selection

This is a **single-job trusted-planner model**, not protection from a compromised
planner. Central/shared code, dependencies, templates and the fixed scan module
are trusted; target YAML is data. Hashes record integrity/provenance, not an
independent proof of policy authorization.

Each target job checks out shared tooling from `main` once and uses that same
checkout for validation, planning and publication. It checks tracked
cleanliness and records the resolved commit SHA in the job summary. The shared
command also derives and checks its own clean runtime revision and records
source/template/policy inputs. Diagnostic reports do not select executable modules.

Shared updates are adopted automatically on later checkouts; a shared regression
can therefore affect consumers without a separate pin-update PR. Different jobs
may resolve different commits if shared `main` advances during a workflow run.
A running job does not refresh its shared checkout. No dirty-source fallback is
provided.

Workflow dispatch and rollout remain separate authorization steps. Local fixture
revisions are test data, not production source selections.

## Workflow operation

The manual workflow permits token-free `preview` runs on feature branches, using
the templates and policy from that branch's dispatched commit. `create-prs` jobs
and publishing-token generation remain restricted to workflow source `main`.
The six targets are `main` plus the five existing release branches (`rhoai-2.25`,
`rhoai-3.4`, `rhoai-3.5`, `rhoai-3.6`, and `rhoai-3.7-ea.1`), kept in the workflow
matrix rather than a second inventory file. Each job invokes shared
`reconcile-tekton` once with the target checkout, target branch, template configuration,
explicit `--policy-python .../scan_resource_policy.py`, and M-only permission.
The nested layout comes from template configuration, not a repeated CLI flag.

`preview` is the default: it never requests an App token and leaves the target
unchanged. In `create-prs` mode, the workflow obtains a repository-scoped App token
before invoking the trusted command with `--publish`. That invocation plans,
runs all five validation phases, checks the exact candidates and publishes
nonempty changes. A token may be obtained even for an empty or failed plan.
Trusted planning runs with that token available; this is not credential isolation.

There is no separate verify/resume operation or saved-plan approval workflow.
A later publishing run recalculates and validates its changes rather than
publishing an earlier preview. Diagnostic result/phase reports, patches and logs
are uploaded, not executable stages or resumable bundles. Checkout credentials
are not persisted; dependency caches are not shared.

Publication keeps M-only exact scope, modes, original/base bytes, provenance,
clean index, origin, remote freshness and staged-blob verification. Preserve
`automation/pipelinerun-conformance/<target-branch>`, `Conformance-Generated: true`,
`create-or-update`, append-only parents and normal pushes. No force-pushes,
automatic merges or manual Konflux reruns are requested. For the `main` target,
`main` is the generated PR base, never a direct-push destination; changes use
`automation/pipelinerun-conformance/main`. Static
[scan review notes](scan-resource-review-notes.md) supplement shared generated
validation/provenance and deferred-case summaries; there is no metadata module.

## Focused tests

With existing shared dependencies, run the consumer's policy/template tests:

```bash
VALIDATOR_CHECKOUT=/path/to/reviewed-shared \
  uv run --project /path/to/reviewed-shared --no-sync --offline pytest -q \
  script/test_scan_resource_policy.py
```

For explicitly local uncommitted library tests only, use
`LOCAL_SHARED_CANDIDATE=/path/to/local-shared-candidate` instead. These tests call
policy and shared template library functions; they do not relax production CLI
source checks. End-to-end candidate CLI testing requires a clean disposable
source fixture with its own recorded fixture commit, not a fake production pin.
Generic phase/context/YAML/source/publication tests belong to shared;
consumer tests retain scan selection, task budgets, owned-field and resource
checks, plus actual template preservation/idempotence/non-fix validation.
