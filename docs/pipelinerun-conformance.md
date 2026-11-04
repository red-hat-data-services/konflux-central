# PipelineRun template conformance

The general template set is `tekton-templates/pipelineruns/config.yaml`. It is
separate from the existing parent-based PR migration templates. Push, PR, and
scheduled PipelineRuns currently share `common.yaml.j2`; component overlays use
the shared validator's existing component directory convention.

Only template-owned fields are reconciled. The first rule sets the
`rpms-signature-scan` **step** to a CPU request of at least `250m`, memory request
and limit of at least `512Mi`, and no explicit CPU limit. Larger existing CPU
requests remain. Memory request and limit use the larger of the existing step's
two memory values and the template floor, so memory is never reduced. Existing
CPU limits, including larger ones, are intentionally removed for this step.
Other steps, task overrides, triggers, image parameters, task pins, service
accounts, and timeouts are preserved. CPU and memory floors live in the template,
not the orchestration script.

## Pinned engine and local commands

Use a clean checkout of `devops-shared-resources` at
`38a4309d165c78d4485b9d54f83066d131761e1a` (PR #51). The runner checks its HEAD
and rejects tracked modifications. Use that project's locked Python dependencies.
The runner registers a process-local `quantity_floor` filter with the shared
engine; it compares Kubernetes quantities using exact decimal arithmetic.

From the central repository, with **separate** checkouts for runner/templates,
validator, and the target release branch:

```bash
uv run --project /path/to/validator --frozen python script/reconcile_pipelineruns.py \
  --path /path/to/release-checkout --validator /path/to/validator \
  --branch rhoai-3.5 --report-dir /tmp/conformance-rhoai-3.5
```

The default is report-only: `report.json` lists every discovered file and its
outcome; `changes.patch` previews proposed changes. Reports must be outside the
target checkout. `--branch` supplies reference context; it does **not** check out
a branch. The caller is responsible for selecting the correct target checkout.

Add `--fix` to apply the planned edits **locally**. No Git commits, pushes, PRs,
cluster queries, or builds are performed by the script. Any planning error blocks
all writes. Writes are sequential, not a filesystem transaction; a filesystem
failure during application may leave a partial local diff. Review the diff before
committing. Run again without `--fix` to confirm conformance.

Run the standalone tests without scanning/validating production pipelines:

```bash
VALIDATOR_CHECKOUT=/path/to/validator \
  uv run --project /path/to/validator --extra dev --frozen python -m pytest \
  script/test_reconcile_pipelineruns.py -q
```

## Conservative handling and reports

- `planned`: applicable PipelineRun has a validated, idempotent patch.
- `conformant`: no changes needed.
- `manual`: existing scan **task-level** `computeResources` budget. No automatic
  mixing of task budgets and step overrides; explicitly reconcile these later.
- `unresolved`: external/dynamic Pipeline reference or missing source. No remote
  resolution or assumptions about a dynamic target branch.
- `skipped`: no scan task, or not a PipelineRun.
- `error`: malformed YAML, duplicate scan identities, unsupported PipelineRun
  classification, invalid quantities, out-of-scope edits, or failed validation.

The runner resolves inline Pipelines and the two known central container-build
Pipeline paths when their revision is the supplied branch or `{{ target_branch }}`.
It does not execute CEL to resolve dynamic revision expressions. `.yaml` and
`.yml` PipelineRuns are discovered in `pipelineruns/*/.tekton/`. Multi-document
files fail closed rather than silently skipping contained PipelineRuns.

Exit code 1 indicates errors. Planned changes, manual cases, and unresolved
references are reported with exit code 0: a successful **preview** is not proof
that the branch is fully conformant or rollout-ready. Review every count.

All initial fixes are guarded to modify only the scan step's four CPU/memory
request/limit fields. This safety guard is intentionally narrower than the general
template engine. Expanding conventions requires a separately reviewed update to
the guard and tests, not just adding a disruptive field to a template.

## Manual workflow and rollout boundary

`pipelinerun-conformance.yml` runs only by manual dispatch on `main`, uses that
workflow commit for runner/templates, and checks out the pinned validator. Its
matrix is the sole rollout branch list:

- `rhoai-2.25`
- `rhoai-3.4`
- `rhoai-3.5`
- `rhoai-3.6`
- `rhoai-3.7-ea.1`

The workflow has read-only repository permissions and produces reports/patches as
artifacts. It never runs `--fix`, pushes, or opens PRs. No secret-backed bot token
is needed. Existing downstream PipelineRun synchronization is unchanged.

**This does not deploy the mitigation.** Before preparing release rollout PRs,
review patches and manual/unresolved cases, verify the exact task digest on each
branch, and authorize a representative runtime comparison. Confirm the admitted
scan pod actually has the intended memory and CPU request and **no CPU limit**;
omission in source YAML does not prove removal of a bundled Task default or an
admission-injected limit. Check scan outcome/duration and scheduling/quota impact.
Only then stage release-branch PRs and use existing downstream sync after merge.
The `512Mi` baseline is provisional, not a guarantee for every image.
