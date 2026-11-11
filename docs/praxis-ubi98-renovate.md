# Default-policy UBI PQC 9.8 updates

The default Dockerfile/RPM policy contains a custom regex manager for
`registry.redhat.io/ubi9/ubi-minimal-pqc`. Its update rules apply to all repositories
using the default policy on `main` and `rhoai-3.6`, rather than only the original
Praxis pilot. Both target branches are already scanned by the default policy;
the shared branch list is unchanged.

Derived presets must retain `custom.regex` in any `enabledManagers` override.
The model-registry preset explicitly enables it so the custom PQC manager can
replace ordinary Dockerfile updates on the selected branches. Existing custom
managers and unrelated image updates are preserved.

No notebooks- or Garak-specific changes are included. The inspected build paths
use AIPCC images selected through `ARG` defaults or build-arg files rather than
literal PQC `FROM` references, so this migration does not target those inputs.
Notebooks already inherits the default policy indirectly through its downstream
configuration; that inheritance and its existing managers remain unchanged.
Independent presets such as Garak and OGX are not made to inherit the default
policy as part of this rollout.

During this development cycle, `rhoai-3.7-ea.1` is automatically synced from
`main`, so it receives the image update through that sync. Direct MintMaker
updates target `main` and the independently maintained `rhoai-3.6` branch.
Updating `rhoai-3.7-ea.1` independently as well would risk conflicts with the
main-to-EA sync.

The custom manager uses the same five Konflux/RHOAI Dockerfile patterns as the
shared policy. Recursive matching first selects a literal `FROM` instruction
(including optional `--platform`), then extracts only its image token. Comments,
`RUN` commands, `ARG` assignments, other images, and explicit non-9.8 tags are
not migrated. Variable-based image references are not handled by this policy.

`currentValueTemplate: "9.8"` directs registry lookup to that tag even when the
existing reference has only a digest. The replacement template writes
`:9.8@sha256:...`. This is a normal digest update, not a same-name replacement
update. The ordinary Dockerfile manager is disabled for this image on the two
selected branches to prevent competing updates. Other branches keep ordinary
shared-policy behavior. PQC updates managed by this policy do not automerge.

## Known limitation

If a digest-only reference already equals the current 9.8 digest, there is no
digest update and Renovate will not add the tag until that digest changes.
The policy is not a universal tag-only migration. Image version labels also do
not establish FIPS certification; builds must still pass `check-payload`.

## Rollout validation

Offline checks validate the rollout rules, repository/branch/manager scope,
model-registry manager inheritance, and extraction/rendering using an existing
Renovate installation. The custom manager and default branch scan list remain
unchanged from the pilot. Test harnesses and logs are local investigation
artifacts, not committed repository tests. No new authenticated multi-repository
dry-run or live rollout has been performed for this change.

## Original Praxis pilot validation

An authenticated `dryRun=full` run against Praxis `main` and `rhoai-3.6`
resolved the 9.8 digest and generated the three expected runtime Dockerfile
updates on each branch. The local runtime was Renovate `43.210.0-rpm`; production
image parity has not been established. The run enabled only `dockerfile` and
`custom.regex` and disabled post-upgrade shell commands. It did not validate
RPM lockfile refresh behavior or execute builds.

The local image reports the inherited shared `rpm` option as invalid. This
policy preserves the shared manager list and does not fix that pre-existing
compatibility issue. The original Actions dry-run using Renovate `44.71.0-rpm`
also reported it, so it is not limited to the older local image. The repository's
`script/run-renovate.sh` currently accepts but does not load `--config-file`,
so the pilot used a temporary launcher that explicitly
merged the local default preset with the MintMaker base configuration,
ignored the published repository config, and forced full dry-run
mode. No runner fix is included in this scoped PR.
