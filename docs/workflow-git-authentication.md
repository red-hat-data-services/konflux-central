# Workflow Git authentication

Implementation scope: [RHAI-9797](https://redhat.atlassian.net/browse/RHAI-9797).

## Credentials and permissions

The four main-defined manual branch-update workflows retain the existing
repository secrets `APP_ID` and `PRIVATE_KEY`, **not** the organization's
`RHDS_CI_APP_*` secrets. This is an intentional implementation decision that
differs from the original ticket. The existing App must remain authorized by the
rulesets coordinated in [RHAI-9806](https://redhat.atlassian.net/browse/RHAI-9806);
do not assume an exemption or bypass for `rhds-ci` covers this App.

- Each writable checkout uses a fresh App token restricted to the current
  repository and Contents write. The default `GITHUB_TOKEN` has Contents read.
- Dry runs do not mint App tokens and use read-only default-token checkouts.
  Existing commit/push guards and skip markers are preserved.
- Read-only nested script checkouts do not persist credentials.
- Jobs time out after 45 minutes, before installation tokens expire after one hour.
- The token action uses the supported Node 24 `v3` major tag, keeping the numeric
  `app-id` input for the existing repository secret. Permissions are explicitly
  reduced using `permission-contents: write`.
- These four workflows do not edit `.github/workflows/**` or explicitly dispatch
  Actions runs, so their tokens do not request Workflows write or Actions write.

The existing `update-repository-list.yml`, `sync-pipelineruns.yml`, and
`sync-renovate-configs.yml` authentication is unchanged. Repository-list updates
modify workflow files and require the existing App's **Workflows write** as well
as Contents write. Cross-repository sync keeps its existing token scope.

## Source and baseline checks (2026-10-15)

The existing App was identified as `rhods-devops-app` from token-action output in
[baseline retrigger run 38399479322](https://github.com/red-hat-data-services/konflux-central/actions/runs/38399479322).
The organization installation metadata shows installation `36825452`, access to
all repositories, and Contents write plus Workflows write. That run still used
default-token Git authentication: the App identity and commit author are **not**
evidence of an App-authenticated push.

Existing successful baseline runs (not validation of this change or protection):

| Unchanged path | Baseline run |
| --- | --- |
| Repository-list updates | [38755654225](https://github.com/red-hat-data-services/konflux-central/actions/runs/38755654225) |
| PipelineRun sync | [38749093839, rhoai-3.6](https://github.com/red-hat-data-services/konflux-central/actions/runs/38749093839) |
| Renovate-config sync | [38500656127](https://github.com/red-hat-data-services/konflux-central/actions/runs/38500656127) |

Release source inspection confirmed that all four modified workflows are absent
from the following branches. Their existing repository-list and PipelineRun sync
writable checkouts already use App tokens; no credential backports are needed.
These branches remain possible manual-dispatch targets from main.

| Branch | Inspected commit | Protected-target execution |
| --- | --- | --- |
| `rhoai-2.25` | `23f932e03511cd3bf985b3de7be969f56dc817f2` | Pending authorization |
| `rhoai-3.3` | `9123e22f9b4fb2ad9634627d47e32672af003e35` | Pending authorization |
| `rhoai-3.4` | `67d29be9bff59cabb20b77cad06406174486131b` | Pending authorization |
| `rhoai-3.5` | `f979a42470136589c510e73b517b5dea414d81bc` | Pending authorization |
| `rhoai-3.6` | `07619ef39c9f85ffd78149138dfa5f54512073a1` | Pending authorization |

## Validation and rollout

Local regression tests require no secrets or GitHub writes:

```sh
uv run --with pytest --with pyyaml pytest script/test_workflow_git_auth.py -v
```

The PR test workflow runs these checks. They verify token-to-checkout wiring and
execute commit/push guards against temporary local bare repositories. This does
not establish installation-token validity or compatibility with branch protection.

Before declaring the migration complete, record authorized GitHub run URLs,
target commits and inputs, effective protection, and the authenticated push App
actor (from server-side event/audit evidence, not merely the commit author):

1. Test z-stream updates and retriggers in dry-run mode, then against authorized
   protected targets. Record release-branch coverage or an equivalent protected
   test target and any remaining gaps.
2. Test architecture reports through the existing manual-dispatch path; include a
   no-change rerun. Do not distribute the workflow to release branches or change
   its push trigger as part of this migration.
3. Test replication as new-branch creation in a disposable authorized target.
   Never recreate a production release branch. Verify dry runs do not create a
   remote branch and existing/unreachable targets are rejected.
4. Verify the three unchanged sync paths in an authorized protected test setup,
   including Workflows write for repository-list updates. Review cross-repository
   targets before enabling downstream sync.
5. Observe downstream runs: z-stream and replication retain `[skip-sync]`;
   retriggers retain their `skip_sync` choice (default true). Architecture reports
   only write `multi-arch-report.yaml`, outside the sync and report push filters.
   Repository-list writes only workflow files, outside its own push filters.
   Confirm that skipped sync jobs do not start component builds, intentional sync
   starts only the expected builds, and no push loops or duplicate builds occur.
6. Share evidence and the retained `rhods-devops-app` identity with
   [RHAI-9806](https://redhat.atlassian.net/browse/RHAI-9806). Keep production
   exemptions during migration testing; exemption removal and final production
   policy validation belong to that follow-up.

No live workflow dispatches, production writes, ruleset changes, or exemption
removals have been performed as part of the local validation above.
