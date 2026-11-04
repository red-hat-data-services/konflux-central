# Enforce PipelineRun templates in component repositories

The manually dispatched **Enforce PipelineRun templates** workflow applies a
selected KC `validate-tekton` template set to component release repositories and
optionally opens PRs. This means template content conformance, not just YAML
whitespace. There is no tenant-specific selection or deletion policy in the
runner. Normal component sync and release-PR generation remain separate.

## Inputs and policy selection

- `release`: one of 2.25, 3.4, 3.5, 3.6, or 3.7 EA.1 (targeting is unchanged).
- `repository`: a managed repository name or `all`. Inventory comes from the
  selected KC release, not main's newer list.
- `template_set`: required, without a default. Select a reviewed directory under
  `tekton-templates/`; `default` explicitly selects the root directory.
- `tracking_issue`: optional Jira key to reference in generated PRs.
- `dry_run`: true by default. Preview never obtains publication credentials,
  commits, pushes, creates PRs, or changes the target checkout.
- `allow_batch_publication`: false by default. Publishing `repository: all`
  requires both `dry_run: false` and this additional explicit confirmation.
  Batch preview does not need confirmation; confirmation alone cannot publish.

The workflow must land on KC's default branch to expose manual dispatch. The
reviewed workflow ref supplies the script and templates; shared tooling comes
from shared-resources main. Sets using `is_pipeline_run` depend on
[shared-resources #52](https://github.com/red-hat-data-services/devops-shared-resources/pull/52).

## Supported template sets

This initial implementation enforces existing files in flat component `.tekton`
directories. Templates may update PipelineRun fields/annotations/references or
signal whole-file removal with the existing `delete_if` filters. Unmatched
PipelineRuns and non-PipelineRun resources are preserved byte-for-byte.

Working-tree parent relationships and overlays inside the selected set are
supported by the validator. Nested KC layouts, external `parent_ref` migrations,
generated-file creation, symlinks, and escaping template references are rejected
rather than guessed. Some existing KC migration sets therefore are not component
policies; choose an appropriate reviewed flat set. The runner neither invents
new formatting rules nor imports policies from component repositories.

## Preview and validation

Start with one repository. The standalone runner strictly inventories YAML and
runs actual validator initial validation, dry-run, fix, independent non-fix
validation, and idempotence in a disposable staging tree. The initial report
identifies matched files and explicit deletion signals. Only matched existing
PipelineRuns may change; only explicitly signalled files may disappear. No new
files or executable-mode changes are permitted. Resources, unmatched files,
and all paths outside `.tekton` remain unchanged.

Example (using the included cleanup set as one possible policy):

```sh
uv run script/enforce_pipelinerun_templates.py prepare \
  --target /path/to/component --tooling /path/to/devops-shared-resources \
  --policy-root tekton-templates --template-set cleanup-odh-tenant \
  --release rhoai-3.6 --repository ai-gateway-controller \
  --tracking-issue RHAI-19583 --report /tmp/new-enforcement-report
```

The report directory must be new and outside the checkout. Artifacts contain
input revisions, selected policy/digest, matched files, update/deletion manifest,
exact patch, after-state hashes, and validator/error reports, not checkout trees.
Failure reports name malformed YAML and retain available input revisions. Valid
resource-only streams survive unchanged; duplicate keys, malformed trailing
YAML, bundled PipelineRuns, unsafe paths, or incomplete fixes block the pair.

## Publication

Publication is a separate command/explicit workflow input. It obtains an App token
scoped to one repository with only contents/write and pull-requests/write. It
rechecks the current release base, exact preview hashes, file scope, validated
after-state, patch digest, and staged changes before a signed-off commit and
feature-branch PR. No force-push, auto-merge, live sync, manual build dispatch,
or Jira field mutation is performed. Ordinary PR CI/Konflux builds may start.

Open PR detection is scoped by repository, release, and template set. Existing
open policy PRs are reported without overwriting them. A prior PR on the same
policy-derived branch requires explicit disposition rather than resurrection.
Superseded manual PRs on different branches do not block an automated replacement.
Batch jobs run with bounded concurrency and preserve individual failures.

## Included policy example

`cleanup-odh-tenant` is one selectable set, not the workflow's identity. Its
template evaluates `delete_if('open-data-hub-tenant')` against the namespace
without rewriting that field. Exact tenant matches are removed; other tenants,
omitted namespaces, and explicit null namespaces remain unchanged. Switching template sets can instead enforce ordinary metadata
or build/reference rules without modifying the runner or workflow.

## Local tests

Use an existing environment with pytest and the script dependencies. Set
`ENFORCEMENT_TEST_TOOLING` to a shared tooling checkout,
and run `script/test_enforce_pipelinerun_*.py`. Tests cover real template updates,
policy-driven deletions, unchanged files, preview/batch safeguards, diagnostics,
idempotence, policy isolation, and publication gates. Remote writes are mocked.
