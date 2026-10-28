# Early Gate — operations

What has to exist before a gate can run, in what order, and what breaks without each piece.

This is the **where**. For why the pipeline is shaped this way see [`design.md`](design.md);
for running a gate once it is up see [`user-guide.md`](user-guide.md).

---

## 1. Dependency order

These cannot be done in parallel. Each step is blocked by the one above it.

```
1. early-gate/ in konflux-central main        ◄── everything resolves tasks from here
2. Konflux Components + secrets                ◄── names are immutable once created
3. .tekton files in rhods-operator / RHOAI-Build-Config
4. operator-processor.yaml EG mode
```

**Step 1 first, always.** Every `.tekton` launcher resolves its pipeline from
`red-hat-data-services/konflux-central`. Until `early-gate/` is on `main` there, nothing
downstream can start.

---

## 2. Konflux Components

Three dedicated Components, all in application **`rhoai-early-gate`**. Created by
[konflux-release-data MR #23609](https://gitlab.cee.redhat.com/releng/konflux-release-data).

| Block | Component | Repository | Builds on push to |
|---|---|---|---|
| 1 | `rhods-operator-eg` | `rhods-operator` | `eg-*` |
| 2 | `rhods-operator-bundle-eg` | `RHOAI-Build-Config` | `eg-bundle-*` |
| 3 | `rhoai-fbc-fragment-eg` | `RHOAI-Build-Config` | `eg-fbc-*` |

Each Component's `containerImage` must match its downstream image repository:

| Component | `containerImage` |
|---|---|
| `rhods-operator-eg` | `quay.io/rhoai/odh-rhel9-operator` |
| `rhods-operator-bundle-eg` | `quay.io/rhoai/odh-operator-bundle` |
| `rhoai-fbc-fragment-eg` | `quay.io/rhoai/rhoai-fbc-fragment` |

Konflux provisions push credentials from the Component's `containerImage`. Update
`tenants-config/cluster/stone-prod-p02/tenants/rhoai-tenant/rhoai-early-gate/resources.yaml`
in konflux-release-data, regenerate manifests, and allow the Components and their
credentials to reconcile before enabling the new destinations in the gate.
Keep the dedicated EG Components and existing `*-eg-*` tags: the repositories are
shared with release builds, but release tags must never be overwritten.

Intermediate snapshot/source artifacts remain in `trusted-artifacts-repository`,
which defaults to `quay.io/rhoai/pull-request-pipelines`. This preserves the
orchestrator service account's existing artifact push access independently of the
three image build Components.

### Component names are immutable

Once created, a Component cannot be renamed. Six things have to agree, and they are set in
different files:

```
appstudio.openshift.io/application   ─┐
appstudio.openshift.io/component      │  in the .tekton file
metadata.name                         │  (the PipelineRun name becomes the check-run name)
output-image                          │
serviceAccountName                   ─┘
*-build-check-name                   ─── in early-gate-component-pipeline.yaml
```

The pipeline's current defaults assume the names above:

| Param | Default |
|---|---|
| `build-check-name` | `rhods-operator-eg-on-push` |
| `bundle-build-check-name` | `rhods-operator-bundle-eg-on-push` |
| `fbc-build-check-name` | `rhoai-fbc-fragment-eg-on-push` |

These match the `metadata.name` of the three `.tekton` PipelineRuns, which is what PaC
reports the check-run as — verified against the files in `rhods-operator` and
`RHOAI-Build-Config`.

> Keep them in step if a PipelineRun is ever renamed. A wrong check-run name does not fail
> fast — the block waits out its full timeout (up to 270 minutes for the operator) on a
> check-run that never appears, which looks exactly like a build that never started.

---

## 3. Secrets and workspaces

The pipeline declares three workspaces. The launching PipelineRun binds them, so the secret
*names* are the tenant's choice — the pipeline does not hardcode them.

| Workspace | Shape | Used for |
|---|---|---|
| `gate-data` | per-run PVC, e.g. a `1Gi` `ReadWriteOnce` `volumeClaimTemplate` | sharing group configuration and reports between TaskRun pods |
| `github-credentials` | secret with a `token` key | branch operations and the workflow dispatch |
| `quay-credentials` | required secret with a `.dockerconfigjson` key | snapshot reads, all three build digest reads, and FBC rendering of the private bundle |

`gate-data` must use storage shared across pods; `emptyDir` is not sufficient. The
launcher uses the tenant's default StorageClass. Verify PVC provisioning, permissions,
and quota before a live run, and include the generated claims in run-retention checks.

The resolver writes `group-components.json`, `invalid-repo-warnings.md`, and
`format-help-warning.md`. Snapshot generation reads the group and writes
`component-table.tsv`, `fallback-warning.md`, and `omitted-components.md`. The audit
and final comment tasks read these files; failure comments tolerate missing files and
can report partial output without referencing a failed task's results.

Only small values are Tekton results. `OMITTED_COMPONENT_COUNT` replaces the former
unbounded `OMITTED_COMPONENTS` pipeline result; the full list stays in the workspace.
The existing `SNAPSHOT_ARTIFACT` and bundle/FBC trusted-artifact flow are unchanged.

When testing a konflux-central branch, set **both** the launcher's PipelineRef revision
and its `konflux-central-revision` parameter to that branch. Otherwise its tasks still
resolve from the parameter's default, `main`.

### The GitHub token needs `actions:write`

`trigger-operator-processor-on-ephemeral-branch` dispatches a GitHub Actions workflow. The
PaC-minted token does **not** carry `actions:write` — it is scoped for git operations. A
separate token is required, or the dispatch fails with a 404 that is indistinguishable
from a missing workflow file.

### Optional: `early-gate-secrets`

`bundle-processor` and `fbc-processor` reference a secret of this name for 13 values —
`registry.redhat.io` credentials, Quay API tokens, a read-only Quay robot. **All 13 are
declared `optional: true`**, so its absence is silent: the pods start, nothing fails.

What is lost without it:

| Keys | Effect when absent |
|---|---|
| `REGISTRY_REDHAT_IO_{USERNAME,PASSWORD}` | `bundle-processor` skips SBOM extraction (`-mc`) with a warning. **The CSV will not carry the SBOM-derived `*_UPSTREAM_VERSION` env vars that the release bundle has.** |
| `RHOAI_QUAY_RO_{USERNAME,TOKEN}` | standalone processor tasks lose their fallback credentials; the pipeline supplies `quay-credentials` to `opm` |
| `RHOAI_QUAY_API_TOKEN`, `REDHAT_USER_WORKLOADS_QUAY_API_TOKEN` | defaulted to `""` to avoid a `KeyError`; largely inert, since the bundle uses `--use-existing-digests` and the FBC's `extract-snapshot-images` is tolerated-on-failure |

The gate passes without it. But the first row is a real divergence from the release bundle,
which is the kind of difference the gate exists to catch — decide deliberately rather than
by omission.

---

## 4. `config/component_repo_map.json`

Not optional, and easy to miss because nothing in the pipeline *references* it by path.

`resolve-group-configuration` fetches it over HTTP from a hardcoded URL:

```
https://raw.githubusercontent.com/red-hat-data-services/konflux-central/main/config/component_repo_map.json
```

A failed fetch is fatal — `curl -sSf` then `exit 1`. So the file has to be on **`main` of
the production repository**, not merely on the branch the pipeline was resolved from.

Its keys are RHOAI *operand* component names, mapping repo → components → Quay paths.
That is a different namespace from the four early-gate Konflux Component names above, and
the two must not be conflated.

The keys are stored **without the release suffix** — `odh-kserve-controller`, not
`odh-kserve-controller-v3-5`. `resolve-group-configuration` appends the suffix at run time
from `rhoai-version`, once to the whole map, so a Y-stream bump is a parameter change
rather than 122 edits.

Two consequences worth knowing:

- The suffix is **major.minor only**. `3.5` and `3.5.2` both give `-v3-5`, because
  component names are never z-stream qualified. The `.tekton` push files carry the
  z-stream, so every consumer of `rhoai-version` has to reduce — and one of them,
  `operator-processor`, had to be taught to.
- With `rhoai-version` empty the keys stay bare and the task says so. Snapshot keys then
  lack the release, which is wrong for a real run but visibly wrong rather than silently
  mismatched.

---

## 5. Downstream `.tekton` files

Three files, each a dedicated early-gate PipelineRun. None may be the release file.

| Repo | File | CEL fires on |
|---|---|---|
| `rhods-operator` | `.tekton/rhods-operator-eg-push.yaml` | push to `eg-*` |
| `RHOAI-Build-Config` | `.tekton/odh-operator-bundle-eg-push.yaml` | push to `eg-bundle-*` |
| `RHOAI-Build-Config` | `.tekton/rhoai-fbc-fragment-eg-push.yaml` | push to `eg-fbc-*` |

Those exact paths are the defaults of `operator-push-pipeline-path`,
`bundle-push-pipeline-path` and `fbc-push-pipeline-component`. They matter twice over: the
gate **rewrites the output-image tag inside these files** on the ephemeral branch, and the
processors rewrite them too. Pointing any of them at a release PipelineRun would edit a
release file.

For the operator, the same pinning step also supplies the build's `rhoai-version`
and selects `pipelines/multi-arch-container-build.yaml` from the matching release
branch of konflux-central: `3.6` becomes version `3.6.0` and revision `rhoai-3.6`;
an explicit patch version is preserved. The source commit and PaC trigger are unchanged.
The release pipeline's `disable-slack-notifications: "true"` preserves the EG build's
no-Slack policy. These edits affect only the ephemeral operator branch, not its source
branch or the orchestration pipeline's own `konflux-central-revision`.

This release-pipeline pin is required for EG pushes. The generic `main` build pipeline
does not forward `rhoai-version` to `rhoai-init`, which permits an empty version for PR
events but rejects it for pushes. Setting the orchestrator's version alone does not
propagate that value into the separate operator PipelineRun.

### Two things that must stay in step

1. **`output-image` in the `.tekton` file and `image-tag` on the matching monitor.** The
   monitor reconstructs nothing; a mismatch is a silent timeout.
2. **The `early-gate:` commit subject and the CEL.** The operator CEL fires on
   `build/operands-map.yaml".pathChanged()` *or* an `early-gate:` subject. The tag-pinning
   step deliberately commits under a `chore(...)` subject so it matches neither and does
   **not** trigger a build. Change one without the other and a silent no-op becomes a build
   of an unprocessed branch.

### No `build-nudge-files`

Absent from all three on purpose. An early-gate build is a throwaway validation of a
branch that is about to be deleted; it must never open nudge PRs.

---

## 6. Pre-flight checklist

```
[ ] early-gate/ merged to konflux-central main
[ ] config/component_repo_map.json reachable at the raw.githubusercontent URL
[ ] Components rhods-operator-eg, rhods-operator-bundle-eg, rhoai-fbc-fragment-eg created
    in application rhoai-early-gate, each configured for its downstream image repository
[ ] *-build-check-name defaults confirmed against the real check-run names
[ ] github-credentials secret exists and its token carries actions:write
[ ] quay-credentials .dockerconfigjson can pull from all three downstream image repositories
[ ] EG Component push credentials have reconciled for their downstream destinations
[ ] trusted-artifacts-repository remains writable by the orchestrator service account
[ ] gate-data is bound to a per-run PVC with a supported StorageClass and sufficient quota
[ ] the three .tekton files are on the branches the gate forks from
[ ] output-image in each .tekton file matches image-tag on its monitor, exactly
[ ] decision taken on early-gate-secrets (SBOM extraction)
```

---

## 7. Known gaps

| Gap | Consequence |
|---|---|
| No in-repo trigger for the gate pipeline | launched explicitly; there is no `/early-gate` comment or `.tekton` entry point. The last thing between a working gate and a usable one. |
| Tags are fixed per leader PR | two concurrent runs for the same leader PR overwrite each other's images |
| `audit-snapshot` results unconsumed | six results produced, none read; it is a logging step despite its name |

---

## 8. Deviations from the release build

**None.** The `.tekton` files in production carry no deliberate deviation: both the
operator and the FBC build the full four-architecture matrix, `skip-checks` is left at its
default so every scanner runs, and hermetic, prefetch and source-image settings match the
release files. That is the point — a gate that scans nothing and builds one architecture
cannot stand in for a release build.

The POC ran x86_64-only and set `skip-fips-check: "true"` for iteration speed. Neither was
carried over.

One consequence of dropping `skip-fips-check`: the FBC pipeline on `rhoai-3.6` offers only
`skip-checks`, which would take `validate-fbc` with it, so the FIPS check runs. It scans
every `relatedImage` and carries its own 6h timeout. If it outlasts the monitor, raise
`fbc-build-timeout-minutes` in the gate — not `skip-checks`.


## Recovery when PaC misses a push

The operator, bundle, and FBC monitors share `monitor-pac-build.yaml`. Each polls
GitHub checks on the exact pushed commit every 30 seconds. If no matching check
appears within 120 seconds, it verifies that the ephemeral branch still points
at that commit, checks GitHub again, and posts a commit comment:

```text
/test <PipelineRun-name> branch:<ephemeral-branch>
```

The command targets the PipelineRun metadata name, so the configured
`build-check-name`, `bundle-build-check-name`, and `fbc-build-check-name` must be
the actual PaC PipelineRun names (GitHub may prepend its check provider name).
The GitHub credential must be permitted to create commit comments, and its
identity must be authorized to issue PaC commands for the target repositories.

Recovery allows three comment attempts, waiting another 120 seconds for a check
between attempts and after the last one. If no check appears, the monitor fails
with a diagnostic instead of waiting hours for a build that never started.
`trigger-wait-seconds` and `max-trigger-attempts` are task parameters; an empty
`branch-name` or zero attempt limit disables recovery for other task consumers.
The overall build timeout still bounds monitoring.

Queued or running checks suppress recovery. Completed failed checks still fail
the gate. GitHub read errors are retried without posting comments, and a branch
that has moved to another commit fails recovery. Comment POSTs are not blindly
retried on HTTP errors or lost responses: the next attempt first checks whether
PaC registered a build. Check pagination and check registration IDs ensure an
older completed check does not hide a newer queued attempt.

PR gated-artifacts-promoter#49 currently pins `eg-pipeline-work` for both the
pipeline and its task definitions. Apply this change to that revision, or update
both launcher pins together to a revision containing the fix before validating
it live.


## Validate downstream image publishing

Roll out the Component destinations and verify their push credentials first. Then
roll out the authenticated monitor/FBC processor and pipeline defaults together,
and update the three EG PipelineRun templates on the branches the gate forks.
Keep the operator's `stable` template aligned through the normal promotion flow
as well. The gate pins the output-image value on each ephemeral branch at run time.

For a fresh gate run, verify the three monitor image refs use their component's
downstream repository and the existing early-gate tag. All digest results must be
nonempty, the bundle CSV must reference the gated operator image, and the rendered
FBC must reference the gated bundle by digest. Verify the images are readable with
the launcher-bound read-only Quay robot; its name is configured by the launcher.
No credential values need to be printed. A green mocked test suite cannot establish
that the live Component push robots or read-only robot have the required access.
