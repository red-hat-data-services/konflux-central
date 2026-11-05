<!-- pipelinerun-template-enforcement -->

Reconcile existing component-repository PipelineRuns with centrally reviewed templates.

Only validated changes selected by the template policy are proposed. Unmatched
resources and retained bytes/modes are preserved. Non-fix validation and
idempotence must pass before publication. Please review the proposed changes;
this automation does not merge PRs or transfer repository ownership.

Central sync may still overwrite direct component changes until ownership is
explicitly migrated. Coordinate rollout with the repository maintainers.
