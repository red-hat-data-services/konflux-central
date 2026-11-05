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
- `filters` supplies the resource-quantity floor filter used by templates.
- `decision(data, context)` selects the scan task (including `finally`), returns
  per-document template variables, and defers task budgets/dynamic references.
- `check(original, candidate, context)` enforces scan-only owned fields and exact
  resource preservation. It explicitly returns `True` or raises.

Callbacks receive detached data and parsed context (`branch`, `documents`), not
filesystem roots, stage/phase/report handles or commands. They do not perform IO,
network resolution, rendering, serialization or publication. Shared code owns
context containment and rejects unsafe YAML before invoking policy.

## Policy and limits

Only `rpms-signature-scan` step resources are owned: CPU request is
`max(existing, 250m)`, memory request and limit are equal to
`max(existing request, existing limit, 512Mi)`, and the CPU limit is absent.
Unrelated tasks, fields and timeouts remain unchanged. Existing task-level
budgets remain manual. Only inline or explicitly allowlisted branch-local
Pipeline references are resolved; external/dynamic/missing references are
reported unresolved, never fetched or evaluated.

The 512Mi baseline is provisional. Removing a CPU limit in source YAML does not
prove the admitted pod lacks a bundled/admission default. Before rollout, review
exceptions and separately validate exact bundles, admitted resources, scan
outcome/duration and scheduling/quota impact.

## Trust and source selection

This is a **single-job trusted-planner model**, not protection from a compromised
planner. Central/shared code, dependencies, templates and the fixed scan module
are trusted; target YAML is data. Hashes record integrity/provenance, not an
independent proof of policy authorization.

Each release job checks out shared tooling from `main` once and uses that same
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

The manual workflow runs on `main` only. Its five release branches remain in the
workflow matrix, not a second inventory file. Each job invokes shared
`reconcile-tekton` once with the target checkout, release, template configuration,
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
`automation/pipelinerun-conformance/<release>`, `Conformance-Generated: true`,
`create-or-update`, append-only parents and normal pushes. No force-pushes,
automatic merges or manual Konflux reruns are requested. Static
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
