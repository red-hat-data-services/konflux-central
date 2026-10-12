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

Three dedicated Components, all in application **`rhoai-early-gate`**:

| Block | Component | Repository | Builds on push to |
|---|---|---|---|
| 1 | `rhods-operator-eg` | `rhods-operator` | `eg-*` |
| 2 | `rhods-operator-bundle-eg` | `RHOAI-Build-Config` | `eg-bundle-*` |
| 3 | `rhoai-fbc-fragment-eg` | `RHOAI-Build-Config` | `eg-fbc-*` |

Every one must push to **`quay.io/rhoai/pull-request-pipelines`** and never to a release
repository (`odh-rhel9-operator`, `odh-operator-bundle`, `rhoai-fbc-fragment`).

This is not a convention — it is the enforcement mechanism. Konflux mints a Component's
push credential from that Component's own `containerImage`, so a Component wired to the
shared PR-pipelines repository is structurally incapable of writing to a release
repository.

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

> **Confirm each against the Component when it is created.** A wrong check-run name does
> not fail fast — the block waits out its full timeout (up to 270 minutes for the operator)
> on a check-run that never appears, which looks exactly like a build that never started.

---

## 3. Secrets and workspaces

The pipeline declares two workspaces. The launching PipelineRun binds them, so the secret
*names* are the tenant's choice — the pipeline does not hardcode them.

| Workspace | Shape | Used for |
|---|---|---|
| `github-credentials` | secret with a `token` key | branch operations and the workflow dispatch |
| `quay-credentials` | `dockerconfigjson` | resolving digests from the private `quay.io/rhoai` |

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
| `RHOAI_QUAY_RO_{USERNAME,TOKEN}` | `opm` runs unauthenticated against quay.io |
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

Its keys are RHOAI *operand* component names (`odh-kserve-controller-v3-5`), mapping repo →
components → Quay paths. That is a different namespace from the four early-gate Konflux
Component names above, and the two must not be conflated.

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
    in application rhoai-early-gate, all pushing to quay.io/rhoai/pull-request-pipelines
[ ] *-build-check-name defaults confirmed against the real check-run names
[ ] github-credentials secret exists and its token carries actions:write
[ ] quay-credentials dockerconfigjson exists for the private quay.io/rhoai
[ ] the three .tekton files are on the branches the gate forks from
[ ] output-image in each .tekton file matches image-tag on its monitor, exactly
[ ] decision taken on early-gate-secrets (SBOM extraction)
```

---

## 7. Known gaps

| Gap | Consequence |
|---|---|
| No in-repo trigger for the gate pipeline | launched explicitly; there is no `/early-gate` comment or `.tekton` entry point |
| Tags are fixed per leader PR | two concurrent runs for the same leader PR overwrite each other's images |
| `audit-snapshot` results unconsumed | six results produced, none read; it is a logging step despite its name |

---

## 8. Deliberate deviations to revert

The gate is currently tuned for iteration speed. Everything not listed here —
`skip-checks`, `build-source-image`, hermetic and prefetch settings — already matches the
release files, on purpose: a gate that scans nothing and builds one architecture cannot
stand in for a release build.

| Setting | File | Current | Cost |
|---|---|---|---|
| `build-platforms` | `rhods-operator-eg-push.yaml` | x86_64 only | three architectures unproven |
| `build-platforms` | `rhoai-fbc-fragment-eg-push.yaml` | x86_64 only | three architectures unproven |
| `skip-fips-check` | `rhoai-fbc-fragment-eg-push.yaml` | `"true"` | FIPS scan skipped. `fbc-fips-check-oci-ta` scans every `relatedImage` and carries its own 6h timeout, which does not fit the monitor budget. Note this is deliberately **not** `skip-checks: "true"`, which would take `validate-fbc` with it — the two are separable for exactly this reason. |
