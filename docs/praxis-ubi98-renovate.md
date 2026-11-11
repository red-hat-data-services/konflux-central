# Praxis UBI PQC 9.8 pilot

The Praxis distribution extends the shared Dockerfile/RPM policy and adds a
custom regex manager for `registry.redhat.io/ubi9/ubi-minimal-pqc` on
`rhoai-3.6` and `rhoai-3.7-ea.1`. Only Praxis is moved to this distribution.
Its branch list is limited to the repository's main and two release branches.

The custom manager uses the same five Konflux/RHOAI Dockerfile patterns as the
shared policy. Recursive matching first selects a literal `FROM` instruction
(including optional `--platform`), then extracts only its image token. Comments,
`RUN` commands, `ARG` assignments, other images, and explicit non-9.8 tags are
not migrated. Variable-based image references are not handled by this pilot.

`currentValueTemplate: "9.8"` directs registry lookup to that tag even when the
existing reference has only a digest. The replacement template writes
`:9.8@sha256:...`. This is a normal digest update, not a same-name replacement
update. The ordinary Dockerfile manager is disabled for this image on the two
pilot branches to prevent competing updates. Main keeps ordinary shared-policy
behavior. Pilot image updates do not automerge.

## Known limitation

If a digest-only reference already equals the current 9.8 digest, there is no
digest update and Renovate will not add the tag until that digest changes.
The policy is not a universal tag-only migration. Image version labels also do
not establish FIPS certification; builds must still pass `check-payload`.

## Local checks

The regression script uses an existing Renovate installation, without network
access or new dependencies. Run from the repository root:

```bash
export RENOVATE_POLICY_JSON="$(< renovate/praxis-ai-grid-renovate.json5)"
podman run --rm -i --pull=never --network=none \
  -e RENOVATE_POLICY_JSON --entrypoint node \
  localhost/mintmaker-renovate:local --input-type=module \
  < script/test-praxis-ubi98-renovate.mjs
```

This validates the new policy additions separately from inherited configuration
and checks real Renovate extraction/rendering for tagged and untagged references,
platforms, aliases, CRLF, adjacent stages, and references that must not match.

An authenticated `dryRun=full` run against both real release branches also
resolved the 9.8 digest and generated the three expected runtime Dockerfile
updates on each branch. The local runtime was Renovate `43.210.0-rpm`; production
image parity has not been established. The run enabled only `dockerfile` and
`custom.regex` and disabled post-upgrade shell commands. It did not validate
RPM lockfile refresh behavior or execute builds.

The local image reports the inherited shared `rpm` option as invalid. This
pilot preserves the shared manager list and does not fix that pre-existing
compatibility issue for other repositories. Confirm runtime compatibility before
merging. The repository's `script/run-renovate.sh` currently accepts but does not
load `--config-file`, so the pilot used a temporary launcher that explicitly
merged the local shared preset and pilot additions with the MintMaker base
configuration, ignored the published repository config, and forced full dry-run
mode. No runner fix is included in this scoped PR.
