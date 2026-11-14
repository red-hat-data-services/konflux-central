# PipelineRun template alignment

Desired policy remains in `tekton-templates/all-pipelineruns/config.yaml` and
`container-scheduled.yaml.j2`, `push.yaml.j2` and `pull-request.yaml.j2`, separate from parent-based PR generation templates.
Container scheduled files use `container-scheduled.yaml.j2`. Container push and main PR files use
copies of the default scan/workspace policy with P02 CEL routing layered on top.
All copies must keep identical resource fields; the guard rejects divergence.
The PR template additionally excludes `linux/ppc64le`, `linux/s390x`, `ppc64le`
and `s390x` from existing `build-platforms` arrays. Supported-platform order is
preserved; an empty result remains an empty array, and a missing parameter stays
absent. Neither the file nor the parameter is deleted when all platforms are excluded. Separate `charts` and `fbc-fragment` entries use `charts.yaml.j2` and
`fbc-fragment.yaml.j2` for both push and scheduled runs: P02 CEL routing plus the
common git-auth workspace only, without RPM-scan overrides. Existing task resource
overrides and scheduling annotations remain untouched. These entries require the
shared engine's registered `is_chart` and `is_fbc` matchers before consumer rollout.
Family entries precede generic event entries under the normal first-match rules.
This template set uses nested layout and no parent inheritance.

## Release-branch PR exclusion

The `pull-request` template entry has `exclude_branches: ["rhoai-*"]`.
Supported container PRs on those branches are reported as `skipped` before field
rendering/CEL/platform checks. Exclusion is terminal, not fallback to a different
template. Both CEL-bearing and legacy annotation-based PR identity are supported.
Other PR families receive the no-op default and remain unchanged.

Skipped files remain byte-for-byte unchanged, appear in phase diagnostics, and
are not selected for the published patch. They receive no scan, workspace,
routing or platform fixes from this alignment policy. This is an explicit policy
boundary, not blanket fail-open behavior: main PR conformance, non-PR eligibility,
YAML/filesystem safety checks and publication checks remain enforced. The workflow is unchanged; allowed operations remain `M` only.

The separate `release-pr` templates and component sync/generation retain their
own validation and ownership rules. This exclusion neither disables component PR
builds nor suppresses failures in release sync. No stored PR files are deleted.

## Power/Z-only PR exception

The explicit directory mapping is:

```yaml
template_overlays:
  - pipeline_dir: pipelineruns/vllm-cpu
    template_dir: pz-only
```

`all-pipelineruns/pz-only/pull-request.yaml.j2` preserves the existing
`build-platforms` array and pins the whole PR expression to `stone-prod-p02`.
It stays on P02 even when the default PR template is changed to P01. Common
scan/workspace conventions are inherited. Missing parameters remain absent;
empty arrays stay empty. Push/scheduled files have no overlay in this directory.

This is an explicit exception, not automatic classification by platform values.
Other Power/Z-only directories require an intentional config mapping. The guard
uses caller-derived `pipeline_path` and `template_overlay`, compiles merged
variants once, and rejects missing/mismatched identities or unrelated changes.
Shared tooling must contain this overlay-context support before rollout.

## Ownership

The shared `reconcile-tekton` command owns inventory/discovery, safe YAML and
alias detachment, readonly branch-local context, classification, template
configuration/overlays, rendering/serialization, disposable stages, all five
phases, scope/mode checks, diagnostic reports and optional PR publication.
There is no consumer reconciliation runner, artifact transport, source loader,
publication adapter or report parser.

There is no selection sidecar or `decision()` callback. Config uses
`guard_only: true` and ordinary registered `is_container_build_push`,
`is_container_build_pull`, and `is_container_build_scheduled` matchers, followed
by a final `is_pipeline_run` entry whose `default.yaml.j2` is a no-op `{}`.
Container matchers recognize the standard container Pipeline paths or inline
Pipelines with a unique scan task in `tasks`/`finally`. Chart/FBC references cannot
fall through into container templates, including on PR events.

Config `context_paths` retains the permitted branch-local definition snapshot for
compatibility assertions in the guard. Shared loads it once and carries it
through staged validation and provenance; no remote source is fetched.

**Shared engine dependency:** the workflow selects `validate-tekton-dev`, not
any feature branch. In addition to the merged chart/FBC support, it requires the
follow-up container matchers, explicit `guard_only` mode, and template branch
exclusions before this consumer can run or merge. There is no automatic fallback
to candidate tooling. Existing selector and legacy callback consumers remain
supported; `guard_only` is an explicit opt-in, not a silent change to those modes.

The only local production Python is the postmerge guard
`script/scan_resource_policy.py`:

- The literal CPU/memory baseline lives in `container-scheduled.yaml.j2`; the
  module reads it once at load time. Push/PR templates must copy its resource fields.
- Matchers own classification; config owns branch exclusion. The guard asserts
  compatibility of the concrete task/workspace writes, including trusted URL,
  single/family local definitions and scan/workspace declarations. It does not
  select templates, resolve remote sources, or generate eligibility decisions.
- `check(original, candidate, context)` permits only the fields owned by the
  selected family. Container entries retain scan/workspace enforcement; routing
  entries must match their exact template CEL. Chart/FBC entries require git-auth
  and routing but reject all task-resource changes, including RPM-scan injection.
- Container push/main-PR CEL and chart/FBC push **and scheduled** CEL are reconciled to
  `konflux_cluster == "stone-prod-p02"`; existing event, branch, path and unrelated
  guards are retained. Existing CEL is required. The PR guard covers the entire
  expression, including the notebook sync bypass. Container scheduled CEL remains
  unowned and unchanged. Unknown paths and inline Pipelines without the scan task
  receive the no-op default. Recognized reference families with invalid repository
  or local-definition evidence fail closed instead of emitting `unresolved`.
  Chart/FBC revisions remain branch-local and their definitions must declare git-auth.

Callbacks receive detached data and parsed context (`branch`, `documents`), not
filesystem roots, stage/phase/report handles or commands. They do not perform IO,
network resolution, document rendering/serialization or publication. The guard
compiles trusted routing scalars and the PR platform expression once at module
load and evaluates them to verify exact candidate routing and platform filtering. Shared code owns context containment and rejects
unsafe YAML before invoking policy.

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
allowlisted branch-local context. Unsupported paths receive no-op templates;
recognized paths with unsupported repositories, dynamic single/family revisions,
or missing/incompatible definitions fail before any strict staged write. Other
repositories and paths do not inherit the multi-arch contract. Unmatched files
are inventoried as unchanged/conformant, not `unresolved`; branch-excluded PRs are
`skipped`. No-op candidates must equal their original data, so the guard rejects
any attempted mutation. This does not claim cluster enforcement for unmatched files.

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

Each target job checks out shared tooling from `validate-tekton-dev`
once and uses that same checkout for validation, planning and publication. It checks tracked
cleanliness and records the resolved commit SHA in the job summary. The shared
command also derives and checks its own clean runtime revision and records
source/template/policy inputs. Diagnostic reports do not select executable modules.

Updates to the selected shared branch are adopted automatically on later
checkouts; this is a moving branch reference, not an immutable SHA pin. A shared
regression can therefore affect consumers without a separate ref-update PR.
Different jobs may resolve different commits if that branch advances during a run.
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

The trusted template config permits publication PR bases only at exact `main` or
`rhoai-*`, where `*` must match a nonempty suffix; `rhoai-`, `master`, feature
branches and other families are denied. Each publishing invocation must also
explicitly allowlist its exact target via `--allowed-branches`; that CLI list is
not a wildcard list. Preview/inventory do not enforce publication policy. This
requires the corresponding shared `publication_branches` support on
`validate-tekton-dev` before the publishing workflow uses this config. The PR base
is not pushed directly: only a distinct validated `automation/` head is pushed.

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
consumer tests cover real container/family classification, terminal branch scope,
compatibility assertions, task budgets, owned-field/resource checks, no-op files,
and actual template preservation/idempotence/non-fix validation. Tests require
the updated shared container-matching/guard-only engine, not the earlier checkout.
