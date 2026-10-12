# Early Gate — documentation

The Early Gate answers one question before anything merges: **do this set of unmerged PRs
actually compose into a working catalog?**

It takes a group of open PRs, builds the operator from them, builds a bundle around that
operator, builds an FBC fragment around that bundle, and reports the result on the PR that
started it.

## Start here

| If you want to… | Read |
|---|---|
| run a gate, write a leader PR, read a verdict, debug a red run | [`user-guide.md`](user-guide.md) |
| understand why the pipeline is shaped this way | [`design.md`](design.md) |
| stand the gate up in a tenant — Components, secrets, order of operations | [`operations.md`](operations.md) |

Each doc has one job. `user-guide.md` is the **how**, `design.md` the **why**,
`operations.md` the **where**.

## Source

- Pipeline: [`../early-gate-component-pipeline.yaml`](../early-gate-component-pipeline.yaml)
- Tasks: [`../tasks/`](../tasks)
- Component map: [`../../config/component_repo_map.json`](../../config/component_repo_map.json)

## The one thing to know first

**The gate builds nothing itself.** It pushes a commit to an ephemeral `eg-*` branch and
lets Pipelines-as-Code run the repository's own `.tekton` build, then follows that build
over the GitHub check-runs API.

That is what makes the gate's build identical to the release build rather than a
reimplementation that drifts, and why it needs no access to whichever cluster the build
lands in. Nearly every other design decision follows from it — see
[`design.md` §1](design.md#the-one-distinction-to-understand-first).
