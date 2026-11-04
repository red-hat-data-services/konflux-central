# PipelineRun template alignment

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

## Shared engine and local commands

Use a clean checkout of `devops-shared-resources/main`. The workflow resolves
`main` once during checkout and uses that same checkout for tests, preview,
application, and revalidation. Each report records its actual `HEAD` commit SHA;
the publisher requires matching resolved SHAs in the apply and recheck reports.
The runner rejects tracked modifications. Use that project's locked Python
dependencies. The runner registers a process-local `quantity_floor` filter with
the shared engine; it compares Kubernetes quantities using exact decimal arithmetic.

This intentionally follows upstream `main` between runs, so a new run may use a
newer validator. Reported SHAs preserve provenance; they are not a version pin.
For reproducing a historical report locally, check out its recorded SHA.

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
  script/test_reconcile_pipelineruns.py script/test_publish_conformance_pr.py -q
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

The **PipelineRun template alignment** workflow (`pipelinerun-template-alignment.yml`)
runs only by manual dispatch on `main`, uses that
workflow commit for runner/templates, and checks out the shared validator from `main`. Its
matrix is the sole rollout branch list:

- `rhoai-2.25`
- `rhoai-3.4`
- `rhoai-3.5`
- `rhoai-3.6`
- `rhoai-3.7-ea.1`

The workflow has two explicit modes:

- **`preview` (default):** generates reports and patch artifacts only. It never
  applies fixes, obtains an App token, pushes, or opens PRs.
- **`create-prs`:** applies safe fixes in a disposable checkout, rereads and
  revalidates them, then calls `script/publish_conformance_pr.py` to create or
  update one PR per affected release branch. This mode uses the existing
  `APP_ID` / `PRIVATE_KEY` GitHub App pattern, scoped to this repository. The App
  must have contents and pull-request write permissions. The generated token
  explicitly requests only `permission-contents: write` and
  `permission-pull-requests: write`; the ordinary workflow token remains read-only. This is an explicit publication action, not a dry-run.

Publishing requires an applied plan plus a clean second-pass report with matching
file hashes. The publisher rejects unrelated/staged/untracked changes, additions,
deletions, stale release checkouts, and unexpected file paths. No changes means
no new PR. Manual and unresolved cases remain untouched and are listed in the
PR description; they do not block publishing the safe subset.

Automation branches are named `automation/pipelinerun-conformance/<release>`.
Updates use append-only merge commits with the current release tip and previous
automation tip as parents; the tree is the freshly validated release checkout
plus the planned fixes. Published history is never force-pushed. Identical trees
on the same base reuse the existing branch/PR. Concurrent branch updates cause
ordinary push rejection, and a tip lacking the tool's generated marker is refused.
This marker is an accident-prevention check, not proof against a hostile actor;
do not manually modify generated branches. Updates replace generated PR text.
Workflow dispatches are serialized to avoid conflicting publication runs.

The publisher performs Git commits/pushes and `gh pr create`/`edit` only when
explicitly invoked; the reconciliation script remains local-only. Local publisher
use additionally requires `GH_TOKEN` and Git push authentication supplied by the
caller. Do not put tokens in command-line arguments or reports. It can be invoked
with `--path`, `--repository`, `--base`, `--source-sha`, `--plan` (the applied
report), and `--recheck` (the report-only second pass). The workflow supplies these
arguments and preserves all logs/reports as artifacts.

No PR is automatically merged and no Konflux rerun is requested. Normal PR CI may
run when branches/PRs are published. Existing downstream synchronization remains
unchanged and runs after approved release-branch merges.

**Preparing PRs does not deploy the mitigation.** Before merging release rollout
PRs, review patches and manual/unresolved cases, verify the exact task digest on
each branch, and authorize a representative runtime comparison. Confirm the admitted
scan pod actually has the intended memory and CPU request and **no CPU limit**;
omission in source YAML does not prove removal of a bundled Task default or an
admission-injected limit. Check scan outcome/duration and scheduling/quota impact.
Only then merge the reviewed release-branch PRs and use existing downstream sync.
The `512Mi` baseline is provisional, not a guarantee for every image.
