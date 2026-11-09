# Update main PR pipelines from release branch push pipelines

The [Update main PR pipelines from release branch push pipelines workflow](../.github/workflows/update-main-pr-pipelines.yml)
runs only when manually dispatched. It updates `main`'s PR pipelines using
push pipelines from the latest release branch (currently `rhoai-3.6`) as a
reference, applying `tekton-templates`. It then opens a PR using `gh pr create`
from a fresh branch named
`automation/update-main-pr-pipelines-<run-id>-<attempt>`.
It never pushes directly to `main`. If no files change, no PR is created.

The source release branch is set explicitly by `parent_ref` in
[`tekton-templates/config.yaml`](../tekton-templates/config.yaml), not detected
automatically. Update that setting when the latest release branch changes.
The shared `validate-tekton` tool performs the template-based updates; this is
not a direct file copy of the release push pipelines.

## Running it

In GitHub Actions, select **Update main PR pipelines from release branch push pipelines**, choose the workflow
branch, and click **Run workflow**. The selected ref supplies `tekton-templates`;
the PR pipelines being updated always come from `main`, and `parent_ref` in the
selected config determines the source release branch.

The workflow must first exist on the repository's default branch for GitHub
to expose manual dispatch. The templates must also be committed to the selected
workflow ref.

## Retriggering individual component builds

The PR template enforces an exact, component-specific comment command:

```yaml
pipelinesascode.tekton.dev/on-comment: "^/build-konflux odh-model-controller$"
```

The command uses the `appstudio.openshift.io/component` label inherited from
the release pipeline, with its version suffix removed (for example,
`odh-model-controller-v3-7-ea-1` becomes `odh-model-controller`). The PR filename
does not determine the command. For example:

- `/build-konflux odh-model-controller` retriggers only the controller pipeline.
- `/build-konflux odh-model-serving-api` retriggers only the serving API pipeline.

The start and end anchors prevent a component command from matching sibling
pipelines. The template replaces existing comment patterns, including broad or
custom patterns. Bare `/build-konflux` no longer retriggers these pipelines.
To retrigger all components in a repository, post a **separate comment for each
component**, using its exact command; there is no shared retrigger-all command.

This takes effect in downstream repositories after running this automation,
merging its generated PR, and syncing the updated PipelineRuns to those repos.
Changing the template alone does not update existing downstream PipelineRuns.

## Dependencies and behavior

- Checks out `red-hat-data-services/devops-shared-resources` at `main`, which
  must contain `scripts/validate-tekton` and its supporting `lib` modules.
- Uses `uv run` to install the validator's inline Python dependencies.
- Fetches the target repository's full history so the templates can read
  parent push pipelines from `origin/rhoai-3.6`.
- Runs the equivalent of:

  ```sh
  uv run ../validator/scripts/validate-tekton \
    --config-dir ../source/tekton-templates \
    --path . \
    -b main --no-checkout --fix
  ```

  from the `main` checkout. The source and validator checkouts are separate
  from the repository where PR pipelines are updated.
- Re-validates from disk without `--fix` before creating a PR. Any remaining
  validation error fails the job and prevents PR creation.
- Includes only `pipelineruns/**/.tekton/*.yaml` changes in the PR. Merging it
  triggers the existing PipelineRun sync workflow.
- Uploads `update-main-pr-pipelines-log` even if validation fails.

The workflow uses the existing `APP_ID` and `PRIVATE_KEY` secrets. The GitHub
App must have **Contents: read and write** and **Pull requests: read and write**
permissions on this repository. Its token allows the resulting PR to trigger
normal checks.

Concurrent runs are serialized. Each run creates a new commit and branch;
existing PR branches are never amended or force-pushed.
