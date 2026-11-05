# Component Repo PipelineRun Reconciliation

The manually dispatched **Component Repo PipelineRun Reconciliation** workflow
previews corrections to component-repository PipelineRuns against a selected KC
`validate-tekton` template set and optionally proposes them through reviewed PRs.
This means template content conformance, not just YAML whitespace. There is no
tenant-specific selection or deletion policy in the runner. Normal component
sync and release-PR generation remain separate.

## Relationship to central alignment and repository validation

**Central PipelineRun Template Alignment** targets KC-managed definitions on
central release branches. **Component Repo PipelineRun Reconciliation** targets
definitions in component repositories and opens PRs there instead.

The longer-term model centralizes policy and tooling, not copies of every
repository's PipelineRuns. Reconciliation proposes fleet-wide corrections;
future **PipelineRun Policy Validation** checks repository changes against the
same policy without silently applying fixes. Repository-level validation and
retirement of centralized management are not implemented by this workflow.

During migration, account for existing KC ownership before publishing direct
component changes: subsequent central sync can overwrite a component correction.
This workflow does not transfer ownership or disable sync automatically.

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
reviewed workflow ref supplies the script and templates; the validator follows
shared-resources main. The generic matcher from
[shared-resources #52](https://github.com/red-hat-data-services/devops-shared-resources/pull/52)
is merged. Publication uses the separate shared publisher introduced by
[shared-resources #53](https://github.com/red-hat-data-services/devops-shared-resources/pull/53),
checked out at independently approved revision
`b8fe79fa5784260b32712bd6730d162d132852c8`. Neither planner artifacts nor the
floating validator revision select publisher code. Pin updates require review.

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
uv run script/reconcile_component_repo_pipelineruns.py prepare \
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
scoped to one repository with only contents/write and pull-requests/write.
The KC adapter verifies the component receipt, patch digest, selected/deleted
PipelineRun scope, retained bytes, and exact after-state. It applies the patch
without staging and constructs the shared publisher's version-1 request.
The pinned shared publisher owns origin/base checks, Git/index verification,
signed-off commits, non-force pushes, PR lookup, and create-or-report behavior.
Both validator and publisher revisions are recorded separately in provenance.
No auto-merge, live sync, manual build dispatch, or Jira field mutation occurs.
Ordinary PR CI/Konflux builds may start.

Standalone publication requires `--target`, `--report`, and `--publisher-root`
pointing to the separate clean pinned publisher checkout. The adapter's `publish`
command does not execute the floating validator. It loads the standalone
publisher module without importing shared `lib.__init__` or validator modules.
It compares source bytes with the pinned Git object and compiles those verified
bytes directly, so ignored/stale Python caches cannot select different code.
`publication-request.json` records the handoff and `publication.json` the result.

This remains a same-job planning/publication workflow. A fixed publisher source
revision is not fresh-runner isolation against a compromised planner process.
It must not be described as equivalent to #4171's separate publication job;
stronger job/artifact isolation is a separate hardening task.

Open PR detection is scoped by repository, release, and template set. Existing
open policy PRs are reported without overwriting them; a valid prepared checkout
is required even for this path. A prior PR on the same
policy-derived branch requires explicit disposition rather than resurrection.
Superseded manual PRs on different branches do not block an automated replacement.
Batch jobs run with bounded concurrency and preserve individual failures.

The legacy `automation/enforce-pipelineruns-*` branch identities,
`<!-- pipelinerun-template-enforcement -->` PR marker, `enforcement.patch`,
workflow concurrency/artifact prefixes, and `ENFORCEMENT_TEST_TOOLING` test input
remain stable. Renaming the entry points must not create duplicate policy PRs
or invalidate previously generated previews.

## Included policy example

`cleanup-odh-tenant` is one selectable set, not the workflow's identity. Its
template evaluates `delete_if('open-data-hub-tenant')` against the namespace
without rewriting that field. Exact tenant matches are removed; other tenants,
omitted namespaces, and explicit null namespaces remain unchanged. Switching template sets can instead enforce ordinary metadata
or build/reference rules without modifying the runner or workflow.

## Local tests

Use an existing environment with pytest and the script dependencies. Set
`ENFORCEMENT_TEST_TOOLING` to an existing validator checkout and
`PUBLISHER_TEST_CHECKOUT` to the separate clean pinned publisher checkout,
and run `script/test_reconcile_component_repo_pipelineruns*.py`. Tests cover real
template updates,
policy-driven deletions, unchanged files, preview/batch safeguards, diagnostics,
idempotence, policy isolation, and publication gates. Remote writes are mocked.
