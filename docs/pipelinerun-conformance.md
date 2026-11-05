# PipelineRun template alignment

Desired policy is in `tekton-templates/pipelineruns/config.yaml` and
`common.yaml.j2`, separate from the parent-based PR migration templates. Push,
PR, and scheduled PipelineRuns share this template. Component overlays use the
shared validator's component-directory convention.

The initial rule sets the `rpms-signature-scan` **step** to CPU request at least
`250m`, equal memory request/limit at least `512Mi`, and no explicit CPU limit.
Preserve larger CPU requests and the larger existing step memory reservation or
limit. Other tasks/steps, fields and timeouts stay unchanged. Existing task-level
budgets require manual review. Local semantic guards mirror this initial rule;
extending the templates also requires reviewing those guards.

## Trusted-planner boundary

This workflow deliberately uses **one trusted job per release branch**. Reviewed
central/shared code and templates are trusted; target YAML is data. The actual
validator, worker and publisher all use one clean shared checkout at the same
reviewed immutable revision. Their roles are recorded separately in provenance.
A SHA/hash is not proof that policy validation happened.

Validator, orchestration and publication are fixed to corrected reviewed commit
`4de549ff57e1e373424aee0ea21c4b997731e7f0` from
[shared PR #53](https://github.com/red-hat-data-services/devops-shared-resources/pull/53).
The standalone loader verifies HEAD and module bytes against that Git object,
then compiles the checked bytes. The renderer loader additionally enforces the
same corrected revision, a clean tracked tree, and source-bound imports; stale
or foreign cached validator modules require a fresh process. It bypasses `lib.__init__` and stale
pycache. No target file or receipt selects executable code or a tooling revision.

There is **no compromised-planner isolation claim**: the planning process and
later publication share a runner. Trusted code could persist state affecting a
later token-bearing step. Delaying and narrowing the App token reduces accidental
exposure; it does not defend against malicious reviewed code, dependencies, or
templates. This intentionally replaces the earlier separate-runner design and
independent post-planning scan-policy rederivation.

## Shared machinery versus local policy

The consumer actually invokes shared `reconciliation.reconcile()`, which owns:

- Exact byte/mode inventory and an isolated disposable stage.
- Initial → preview → fix → non-fix validation → idempotence phases.
- Readonly branch-local context and phase/index/no-out-of-scope-write checks.
- Exact existing-file change detection, diagnostic patch and data-only bundle.

Local callbacks retain PipelineRun discovery, applicability, task-budget deferral,
per-file template inputs/rendering, semantic phase results, safe YAML/alias handling,
resource preservation and owned-field checks. Target-derived component overlays
must resolve within the trusted template-config directory, including symlinks;
labels cannot select external template code. These checks run during trusted planning,
not as an independent publication security boundary. `conformance_artifact.py`
now holds those policy/report guards, not a second transport implementation.

Publication replays the same-job trusted receipt through shared
`prepare_bundle()`. Its mandatory caller authorizer accepts only the receipt's
M-only PipelineRun rows. It does **not** treat hashes as independently proving
policy or re-render templates. Shared replay checks original Git bytes, hashes,
modes, base, scope, provenance, clean index and unrelated changes before copying
any candidate. The shared publisher then verifies the complete checkout against
the exact manifest and origin, checks remote freshness and staged blobs, and
performs normal non-force publication. Replay can be repeated on exact candidates.

## Local commands

Use separate checkouts for central source/templates, one corrected fixed shared
checkout for all validator/tooling roles, and a **complete clean Git checkout**
of the release base.
Reports must be disjoint from the target and the report directory must not exist.
`--branch` supplies reference context; it does not check out a branch.

```bash
uv run --project /path/to/reviewed-shared --frozen python script/reconcile_pipelineruns.py \
  --path /path/to/release-checkout --validator /path/to/reviewed-shared \
  --publisher /path/to/reviewed-shared --source-sha <workflow-commit-SHA> \
  --branch rhoai-3.5 --report-dir /tmp/alignment-rhoai-3.5
```

The default writes only a disposable stage and reports; the target checkout stays
unchanged. Outputs are `report.json`, `phases/*.json`, diagnostic
`enforcement.patch`, and `bundle/`. The shared version-1 manifest records base OID,
provenance and exact M-only hashes/modes; `files/<path>` contains candidate bytes.
Consumer-owned `policy-report.json` records semantic diagnostics. Reports/statuses
are not independent attestations. The bundle stays local; there is no cross-job
transfer or executable-stage upload.

`--fix` explicitly replays the checked trusted plan **locally**, with no commits,
pushes, PRs, remote fetches, cluster queries or builds. Planning requires a clean
base: do not plan again over its local diff. Idempotence was already checked in
the isolated stage. Filesystem application is not transactional; on an OS failure,
discard the disposable checkout and prepare a fresh one. To repeat exact replay
and publication verification without writing remote systems:

```bash
uv run --project /path/to/reviewed-shared --no-sync --offline python script/publish_conformance_pr.py \
  --path /path/to/release-checkout --repository red-hat-data-services/konflux-central \
  --base rhoai-3.5 --source-sha <workflow-commit-SHA> \
  --publisher /path/to/reviewed-shared --artifact /tmp/alignment-rhoai-3.5/bundle
```

Only separately authorized `--publish` permits Git/GitHub writes. Callers supply
Git/gh authentication and author/committer identity; never put secrets in arguments
or receipts. The CLI's optional `--github-output` writes `ready_to_publish=true`
only after successful exact verification of nonempty changes; no-op/failure never
requests a publishing token. Preview does not invoke replay/publication.

```bash
VALIDATOR_CHECKOUT=/path/to/reviewed-shared \
PUBLISHER_CHECKOUT=/path/to/reviewed-shared \
  uv run --project /path/to/reviewed-shared --extra dev --frozen python -m pytest \
  script/test_reconcile_pipelineruns.py script/test_publish_conformance_pr.py \
  script/test_conformance_artifact.py -q
```

## Outcomes and publication workflow

`planned` means a candidate checked by trusted planning; `conformant` means no
change needed. `manual` leaves task budgets untouched; `unresolved` leaves
external/dynamic or missing Pipeline references untouched; `skipped` covers other
kinds or Pipelines without the scan task. `error` includes malformed/multidocument
or unsafe YAML, duplicate identities, invalid quantities, unsupported classification
or out-of-scope edits. Errors block the plan. No PipelineRuns found is an error.
A successful preview does not establish complete branch alignment.

Only inline Pipelines and the two known branch-local central build Pipeline paths
are resolved for this branch or `{{ target_branch }}`. No remote fetching or CEL
interpretation occurs. Discovery includes `.yaml` and `.yml` under
`pipelineruns/*/.tekton/`.

The **PipelineRun template alignment** workflow
(`.github/workflows/pipelinerun-template-alignment.yml`) is manually dispatched on
`main` only. Its matrix is the sole release inventory: `rhoai-2.25`, `rhoai-3.4`,
`rhoai-3.5`, `rhoai-3.6`, `rhoai-3.7-ea.1`.

- `preview` (default): shared isolated planning and diagnostic artifacts only;
  no target changes, App token, or publication.
- `create-prs`: replay and verify the trusted plan first, then mint a repository-
  scoped App token **only for verified nonempty changes**. Requested permissions
  are contents/pull-request write. Checkout credentials are never persisted.
  Only the final publishing step receives `GH_TOKEN`; it configures
  `gh auth setup-git` and explicitly uses `--publish` with bot Git identity.

No duplicated setup or cross-job bundle transfer is needed. Diagnostic uploads
include reports, patch, manifest and logs, not the executable stage. Workflow runs
are serialized. Generic orchestration stays in shared callable code, not YAML.

Keep lifecycle `create-or-update`, branch
`automation/pipelinerun-conformance/<release>`, and exact legacy marker
`Conformance-Generated: true`. Repository/base-scoped PR lookup also matches exact
heads, including legacy bodies. Generated updates retain the current base and
previous automation tip as parents. Identical states reuse the head. Marker checks
prevent accidents, not hostile actors. Unmarked heads, ambiguous inventories,
stale bases and concurrent updates fail closed. Empty plans create no PR. Partial
push/PR failure is reported, not rolled back. No force-push, auto-merge, build or
workflow dispatch is requested; normal PR CI may run after publication.

## Rollout boundary

**This infrastructure does not deploy the mitigation.** Before merging release
fixes, review patches/exceptions, verify exact task bundles, and separately
authorize runtime comparisons. Confirm admitted scan pods have intended CPU/memory
requests and **no CPU limit**: omission in YAML does not prove removal of bundled
or admission defaults. Compare outcome/duration, scheduling and quota impact.
`512Mi` is provisional. Merges and downstream synchronization need separate approval.
