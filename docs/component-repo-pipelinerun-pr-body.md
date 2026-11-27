<!-- pipelinerun-template-enforcement -->

## Proposed updates

Align existing component-repository PipelineRuns with the selected central template
set. The generated context identifies the workflow run, template directory and
exact source revision, and separates modified files from deleted files.

For the `cleanup-odh-tenant` template set, the proposed update is deletion of
PipelineRuns whose explicit execution namespace is exactly `open-data-hub-tenant`.
Other namespaces, omitted/null namespace fields and non-PipelineRun resources are
not retirement targets. Other template sets apply their own selected policies;
the template link and changed-file list identify the scope of this PR.

Only validated changes selected by the template policy are proposed. Unmatched
resources and retained bytes/modes are preserved. Non-fix validation and
idempotence must pass before publication. Please review the proposed changes;
this automation does not merge PRs or transfer repository ownership.

Central sync may still overwrite direct component changes until ownership is
explicitly migrated. Coordinate rollout with the repository maintainers.
