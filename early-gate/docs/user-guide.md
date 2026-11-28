# Early Gate — user guide

How to run a gate, write the PR that drives it, read the verdict, and work out what went
wrong.

This is the **how**. For why the pipeline is shaped the way it is, see
[`design.md`](design.md). For standing the gate up in a new tenant, see
[`operations.md`](operations.md).

---

## 1. What the gate does for you

You have several PRs that have to land together — a kserve change, a dashboard change, an
operator change. Individually each one is green. The question the gate answers is whether
they **compose**:

```
your PRs ──► operator built from them ──► bundle around that operator ──► FBC catalog
                                                                              │
                                              "yes, this assembles" ◄─────────┘
```

It produces a real, installable catalog fragment built entirely from unmerged code, and
posts the verdict back on the PR that started it.

What it does **not** do: install that catalog or run tests against it. The gate's answer
is that the artefacts assemble and reference each other correctly, not that they work.

---

## 2. Running a gate

The gate is launched against a **leader PR**. At minimum:

```
leader-pr-repo:   red-hat-data-services/<promoter-repo>
leader-pr-number: <n>
rhoai-version:    3.5
```

`leader-pr-repo` and `leader-pr-number` are required — the pipeline declares no defaults
for them. Everything else has a default; the full list is in
[`design.md` §8](design.md#8-parameters-reference).

> **Known gap:** there is no in-repository trigger for the gate pipeline itself — no
> `.tekton` PipelineRun, no `on-comment` annotation. It is started explicitly. Wiring a
> trigger is open work.

A run takes roughly **45–90 minutes** when the operator build is warm. The launcher budget
is 10 hours, which covers the sum of every monitor budget plus the work between them,
because the blocks run in series.

---

## 3. Writing a leader PR

The gate reads the PR **description**, not its diff. Give it a heading matching
`child.*prs` and list the child PRs as full URLs:

```markdown
## Child PRs

- https://github.com/red-hat-data-services/kserve/pull/4621
- https://github.com/red-hat-data-services/kubeflow/pull/81
- https://github.com/red-hat-data-services/odh-model-controller/pull/65
```

Rules, all enforced by `resolve-group-configuration`:

| Rule | What happens if you break it |
|---|---|
| URLs must be full `https://github.com/<org>/<repo>/pull/<n>` form | the PR is not picked up |
| The org must be in `allowed-child-orgs` (default `red-hat-data-services`) | the PR is rejected — the test is an exact whole-string match per line, not a substring, so a lookalike org cannot slip in |
| The list ends at the next `##` heading | anything below it is ignored |
| The repo must appear in `config/component_repo_map.json` | a warning, and that repo contributes no components |

Two things worth knowing:

- **The leader PR's own components are always included first**, before any child.
- **An unreadable child PR degrades, it does not fail.** You get a warning and a smaller
  group, not a dead pipeline. Check the `resolve-group-configuration` log if the group
  looks short.

### Which branch the gate forks from

Decided from the leader PR body, not from a parameter:

| If the group… | Block 1 forks from |
|---|---|
| includes the operator repo | `main` — that is where the operator PR will land |
| does not | `stable` |

So a leader PR listing only component PRs gates them against `stable`; one that also
carries an operator PR gates the whole set against `main`.

---

## 4. Reading the verdict

The gate posts a comment on the leader PR covering all three blocks.

### Read the component table first

```
| PR     | Component                | Tag       | Status        | Digest     |
| —      | odh-kserve-controller    | rhoai-3.5 | Using release | sha256:63a13 |
```

| Status | Meaning |
|---|---|
| **PR build ready** | the component's PR image was found, its `git.commit` matched the resolved PR head, and it was used — genuinely tested |
| **Using release** | the requested PR is closed/merged and no active main/master-to-stable replacement exists; the **released** baseline was used — **this component's PR code was not tested** |

This is the single most important line in the verdict. A run where most components say
`Using release` is mostly re-validating the last release. A green gate with 14 of 14
fallbacks proves the plumbing works and almost nothing about the PRs.

The PR column links only verified PR images. Release-fallback rows show `—`;
their requested collaborator PR links are retained in the collapsed fallback details.

Allowed release rows retain their historical collaborator links in the collapsed
details and explain that no active sync PR exists. Missing checks do not prove
that a pipeline never triggered.

For an active PR, a separate **Required collaborator PR images blocked** section
reports failed/pending/missing builds, lookup errors or unusable images. Read
the collaborator/build links and snapshot logs, fix the problem, then rerun EG.
GitHub PR and required-build lookups are mandatory for admission; errors block
fallback. Any additional historical fallback diagnostics remain informational.

### Then the artifacts

```
Block 1 · Operator   odh-operator-eg-5           sha256:1236…
Block 2 · Bundle     odh-operator-bundle-eg-5    sha256:f4d1…   ✅ verified
Block 3 · FBC        rhoai-fbc-fragment-eg-5     sha256:4a20…   ✅ verified
```

All three live in `quay.io/rhoai/pull-request-pipelines`, told apart by the component
prefix. The tag carries the leader PR number and nothing else, so it is stable and
readable — see [`design.md` §11](design.md#11-image-tagging-strategy).

Install from the **Block 3** image. The by-digest pull refs are in the collapsed section
of the comment.

> Tags are fixed per leader PR. Two concurrent runs for the same leader PR overwrite each
> other's images. The uuid on the ephemeral branches prevents branch collisions, not tag
> collisions.

---

## 5. Troubleshooting

**`update-group-snapshot`** ensures the bundle inputs are consistent with the snapshot. It
injects the operator digest from Block 1 and iterates all other gated components in
`snapshot.json`, updating their `RELATED_IMAGE_*` entries in `bundle-patch.yaml` by matching
on image basename. If a gated component has no matching entry, a warning is logged but the
task continues.

| Symptom | Cause | What to do |
|---|---|---|
| Gate waits forever on a build that never appears | the push did not match the PaC CEL | check the commit subject starts `early-gate:` and the branch prefix is right |
| **No check-run registered at all** | a CEL **parse** error — the whole annotation becomes unevaluable, every arm dies, and PaC skips the PipelineRun *silently* | check `\\.` escaping in the CEL; from the gate this looks identical to a build that never started |
| A block times out instead of failing | the published tag is not the one the monitor searched for | compare `output-image` in the `.tekton` file against `image-tag` on the monitor — they must match character for character |
| `update-group-snapshot` refuses to run | Block 1's reference is not digest-pinned | check whether `monitor-operator-build` actually read a digest back |
| `verify-bundle-images` fails with "bundle is carrying a non-gated build of a gated component" | A gated component's digest in the bundle doesn't match the snapshot. This typically means `bundle-patch.yaml` on the release branch had stale digests. `update-group-snapshot` now auto-pins all gated component digests from the snapshot; if you still see this, check that the component has a `RELATED_IMAGE_*` entry in `bundle-patch.yaml` — components referenced only in the CSV template are not auto-pinned. | review `update-group-snapshot` logs for "no matching entry" warnings, and verify the component's image basename matches a `RELATED_IMAGE_*` key in `bundle-patch.yaml` |
| `fbc-processor` fails on an empty `BUNDLE_IMAGE_DIGEST` | `monitor-bundle-build` warns rather than fails when it cannot read a tag back, so the handoff silently did not happen | look at the bundle build, not the FBC one |
| `extract-snapshot-images` warning in Block 3 | **expected** — it looks the tag up under `quay.io/rhoai` and wants a cosign `.sig` beside it; an early-gate image is neither | ignore; the run continues by design |
| A component you expected is missing | `resolve-group-configuration` degrades rather than fails | check its log for child-PR warnings, and that the org is allowed |
| `pin-output-image` or `push-bundle-to-ephemeral-branch` red | the `.tekton` file named by the pin params is not on the ephemeral branch, or its `output-image` is not in the shape the substitution expects | the error names the file and the expected value |
| Everything green but 14 of 14 `Using release` | no child PR had a current build | see §4 — this is not a gate failure, it is an empty gate |

### Inspecting a run after it finishes

The ephemeral branches are deleted by `finally`, even on failure. To keep one:

```
defer-fbc-branch-cleanup: "true"      # keeps eg-fbc-<pr>-<uuid>
defer-bundle-branch-cleanup: "true"   # keeps eg-bundle-<pr>-<uuid>
```

Delete them by hand afterwards — nothing else will.

### One trap when reading a red run

The git resolver resolves each `taskRef` when that task **starts**, not when the pipeline
starts. A merge to `konflux-central` main can therefore land mid-run, and two tasks in the
same run can come from different revisions. Pin `konflux-central-revision` if you need a
run to be reproducible.


### Required PR images (RHAI-19869)

Every open collaborator PR explicitly listed for this run requires its current
head's successful component build and a readable image with matching
`git.commit`, source labels and a digest. A failed, cancelled, skipped or
otherwise unsuccessful required build fails the gate, even if an image tag
exists. Missing/pending checks and image publication have a bounded wait
(`pr-readiness-timeout-seconds`, default 120 seconds per phase).

An active PR never falls back to the release image and is not silently omitted
when its image is missing, stale, inaccessible or lacks required labels.
A GitHub lookup error is an unknown state and blocks fallback.

Closed/merged collaborator links allow release fallback only after an
independent query confirms no open same-repository `main`/`master` →
`stable` PR exists. If a replacement exists, refresh the Leader and rerun EG;
the gate does not silently choose an unlisted replacement's code.
If a PR head changes after group resolution, rerun EG for the new head.

The failure comment lists blocked components and their collaborator/build
links. No snapshot trusted artifact is published when required images are
blocked. `fallback-tag` selects the baseline tag; it does not override this policy.

Build readiness currently uses current-head Konflux GitHub check runs from
`konflux-internal-p02`. Missing checks do not prove a pipeline never triggered.
If GitHub is stale relative to Konflux, inspect the actual Konflux run before
retrying; changing the authoritative build-status source is separate work.
