# Release push → PR maintenance

The **Maintain release PR pipelines** workflow is manual and dry-run by default.
It invokes the shared **validate-tekton** executable with --fix, then invokes
it again without --fix to re-read/validate the result. There is no separate
shared release-maintenance CLI.

## Sources and ownership

- Target release checkout: push PipelineRuns and existing PR filenames/comment triggers.
- Selected KC workflow commit: templates/policy in tekton-templates/release-pr.
- Shared-resources tooling_ref: validator, template renderer, opt-in child
  creation support, and the release-aware CEL filter.

The policy opts the PR template into generated-child maintenance. Push files
remain read-only. Each PR is rebuilt from its release push build settings, not
from main. Labels, rhoai-version, Dockerfile, platforms, prefetch inputs, and
service-account settings follow the push. KC's PR template defines source and
revision, ephemeral image tags, expiry, disabled Slack notifications, git-auth,
exact release pipeline revision, and release-scoped PR CEL.

The template excludes additional release tags/labels. Push metadata/annotations
are not blindly copied. Existing PR filename/comment triggers survive; existing
label triggers are not migrated. Other child-only build settings are discarded.
CEL retains push path conditions/guards and adjusts the PipelineRun filename.

## Running

Inputs:
- release_branch: exact rhoai-x.y or rhoai-x.y-ea.z target.
- repository: optional pipelineruns directory; empty selects all.
- dry_run: true generates reports/diffs only; false creates a fresh feature
  branch and PR targeting the release.
- tooling_ref: shared-resources ref containing generated-child support.
  Prefer a reviewed immutable commit where practical.

The orchestrator script/maintain_release_pr.py runs the two validator commands,
checks changed-file scope, and records fix/validation JSON, source revisions,
and a diff. Unsupported or ambiguous input fails before PipelineRun writes.
Orphans are reported, never deleted. Dry runs do not request a publication App
token, commit, push, or open a PR. --publish is a separate explicit operation.
Non-dry runs require App Contents and Pull requests write access.

## Rollout

1. Land shared validator changes first, or select a reviewed feature commit.
2. Land this workflow/policy on main so manual dispatch is available.
3. Dry-run one repository on GA and EA releases; review reports and diffs.
4. Confirm the target release's Sync Pipelineruns workflow is present, enabled,
   and handles added/modified YAML paths and same-branch distribution.
5. Run with dry_run=false only when PR creation is intended, then review/merge.
   Existing same-branch synchronization distributes the files to component repos.

No schedules, auto-merge, file deletion, infra merge changes, or promotion of
main build settings are included.

## Local execution

From separate tooling, policy, and target release checkouts:
```sh
uv run ../devops-shared-resources/scripts/validate-tekton \
  --path . --branch rhoai-3.6 --no-checkout \
  --config-dir ../kc/tekton-templates/release-pr \
  --pipeline-dir pipelineruns/kserve --fix
# Run the same command again without --fix to validate from disk.
```
The shared validator never publishes changes. Existing configurations without
the generate option retain their current behavior.
