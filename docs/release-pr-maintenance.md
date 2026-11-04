# Release push → PR generation during component sync

Release PR PipelineRuns are derived during **Sync Pipelineruns**. They do not
need to exist in KC release branches, and no separate KC maintenance PR is
created. Main's existing PipelineRun copy behavior is unchanged.

## Ownership and execution

- KC release branch: authoritative push build configuration.
- KC main: `script/prepare_release_sync.py` and `tekton-templates/release-pr` policy.
- Shared-resources main: the reviewed `validate-tekton` generated-child support.
- Component's matching release branch: push files plus generated PR files,
  distributed together by the existing sync commit/push step.

The sync matrix job checks out these sources separately. Before its existing
commit step, it stages the component's KC YAML files in an isolated directory,
then seeds PR filenames/comment settings from the component's current `.tekton`.
KC push files remain authoritative; component push edits are not inherited.

The preparation script runs the actual shared validator with `--fix`, then
without `--fix` to validate files read from disk, then `--fix` again to prove
idempotence. Only after all checks succeed are staged YAML files copied into
the component checkout. The existing commit step commits push and PR changes
together. Existing manual `dry_run` behavior still skips the push and shows the
staged Git diff; it does not prevent local preparation or the local sync commit.

The PR template preserves release component/application labels and push build
settings. It overrides PR source/revision, ephemeral output tags, expiry,
notification/authentication policy, pipeline revision, and release-scoped CEL.
Additional delivery tags/labels are removed. Build configuration is never
borrowed from main.

## Safety and reports

Generation failures stop the job before committing or pushing. Preparation
never commits, pushes, fetches, or deletes component files itself.

- Non-PR input files must remain byte-for-byte unchanged during generation.
- Missing, ambiguous, or duplicate parent/child matches fail closed.
- Unclassified orphan PRs block rollout pending migration review; they are not deleted.
- Explicitly separately managed downstream E2E/legacy CI files are preserved without
  modification under `tekton-templates/release-pr/ownership.yaml`. Exceptions are
  scoped to the audited branches and require exact application, component,
  pipeline URL, image repository, Dockerfile, and build-context identities.
  Reviewed release-specific identity differences use `by_branch`; unknown keys,
  stale identities, and collisions with generated/source files fail closed.
  Ownership exceptions are listed in `ownership.json` and the final report.
  They establish who manages a file, not that its existing CI definition is valid.
- Unsafe paths, symlinks, unsupported parent settings, and duplicate YAML keys
  are rejected rather than silently repaired.
- PR names must satisfy the Tekton 63-character constraint after substituting
  a ten-digit PR number, with unique names within each component. The template
  reserves 36 characters for the component-derived prefix, 17 for
  `-on-pull-request-`, and 10 for the number. Prefixes that already fit remain
  unchanged; longer prefixes become a readable prefix plus `-` and eight
  lowercase SHA-256 hex characters of the original full prefix. Only
  `metadata.name` changes: filenames, component/application labels, images, and
  triggers remain unchanged. Exact component-label matching keeps shortened
  names associated with their original parent on subsequent runs. Name checks
  verify the expected hash from the full component identity or a supported
  alias, rather than accepting arbitrary hash-looking names.
- Repositories without push or PR pipelines are reported separately as
  `no-push-pipelines`, not counted as successfully generated builds.

Fix, validation, idempotence, and preparation reports are uploaded per matrix
job. Temporary staging trees are not included in uploaded reports. Existing
component files outside the synchronized YAML set are left intact.

Valid non-PipelineRun multi-document resource streams are read strictly and
preserved byte-for-byte. Streams containing a PipelineRun are unsupported and
fail closed, as do duplicate keys or malformed trailing documents. Legacy PR
names containing the exact `-{{target_branch}}` release suffix are recognized
without stripping arbitrary placeholders or relaxing duplicate-match checks.

Definitions explicitly targeting `open-data-hub-tenant` are being removed via
separate, one-time component cleanup PRs, not preserved or deleted by sync.
See the [cleanup plan](release-pr-cleanup-plan.md) and
[pinned audit results](release-pr-sync-audit.md). RHOAI 3.6 cleanup is not GA-gated.
Candidate audit success does not certify unchanged live component branches.

## Rollout prerequisites

1. Land shared validator support first.
2. Land policy/orchestration on KC main.
3. Validate all managed component repositories for each target release using
   sparse `.tekton` checkouts and isolated KC release snapshots.
4. Resolve every failed repository, including migration decisions for unmatched
   component PR files, before enabling publication for that release.
5. Propagate the updated sync workflow to the target KC release branches;
   updating main alone does not update their existing workflows.
6. Run the existing sync manually with `dry_run: true` and inspect diffs/reports
   before allowing its normal component pushes.

No live sync or builds are needed for the local generation audit. Local audit
runs must not commit or push component changes. Preserve repository/branch
inventory, source revisions, sparse-checkout errors, generation errors, and
per-repository output reports; missing coverage must not be reported as success.

## Local preparation

With an existing environment containing the shared validator dependencies:

```sh
uv run script/prepare_release_sync.py \
  --source /path/to/kc-release-checkout \
  --target /path/to/component-release-checkout \
  --tooling /path/to/devops-shared-resources \
  --policy tekton-templates/release-pr \
  --branch rhoai-3.6 --repository kserve \
  --report /tmp/kserve-release-pr-report
```

This writes only generated staging/report files and, on successful validation,
the target checkout's `.tekton` YAML files. It does not publish anything.
