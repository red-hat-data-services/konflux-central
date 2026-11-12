# UBI 9.8 post-migration policy

This is the proposed steady state after [migration PR #4390](https://github.com/red-hat-data-services/konflux-central/pull/4390).
Do not adopt it until the downstream tag-and-digest migration PRs have merged.
The proposal does not establish that migration has completed.

## Ownership

| Source form | Steady-state manager |
| --- | --- |
| Literal tagged FROM | Built-in `dockerfile` |
| Unquoted or double-quoted complete image ARG used in FROM | Built-in `dockerfile` |
| Gateway's separate repository/tag ARGs | Built-in `dockerfile` |
| Praxis's digest ARG referenced by a tagged FROM | Built-in `dockerfile` |
| External argfiles, including OpenVINO's `.konflux/build-args.conf` | Unmanaged by this policy |

No custom UBI managers remain. Supported Dockerfiles use the built-in manager;
external argfiles are neither migrated nor refreshed by this policy. OpenVINO's
argfile-selected builder and runtime bases therefore remain unmanaged, even
if Dockerfile defaults change. No synthetic versions, latest-to-9.8 conversion,
tag insertion, or coupled Praxis/gateway migration handlers remain.

The installed Dockerfile parser resolves the gateway and Praxis variables and
renders digest changes into their ARG definitions. Single-quoted complete
image ARGs are a parser limitation in the tested version, but none occurs in
the 150 captured normalized source fixtures. No speculative custom fallback
or associated native disable rule is included.

Explicit native `managerFilePatterns` and `includePaths` preserve the migration
file scope. Native defaults miss names such as `agent.Dockerfile.konflux`.
The existing exact `Containerfile.server.konflux` exception is preserved;
there is no Containerfile pattern generalization.

## Version and safety policy

For the twelve approved image repositories in supported Dockerfiles, references
already on 9.8 receive the following policy on every current `baseBranches`
entry: `main`, `rhoai-2.25`, `rhoai-3.4`, `rhoai-3.5`, and `rhoai-3.6`:

- `allowedVersions: /^9\.8$/` and digest pinning;
- no automerge and no ignoring CI failures;
- ordinary digest refreshes rather than migration updates.

The constraint matches an existing 9.8 version. Explicit other numbered tags
retain ordinary behavior; new UBI 10 repository names are outside the exact
image allowlist. This is not a mechanism to repair every future missing pin.
Existing manager update-type settings and derived-preset overrides are
preserved; this change does not introduce new version-bump enable rules.

`rhoai-3.3` is excluded from the shared scan list for every configured manager.
OpenJDK remains excluded from this policy. There are no direct EA updates. Existing default-distribution mappings are unchanged.

## Validation and merge prerequisites

Offline checks used installed Renovate **43.210.0-rpm**, not workflow version
44.71.0-rpm. Synthetic already-migrated fixtures were generated from the 150
pinned source snapshots using the migration renderer; placeholder digests are
not registry evidence or real component changes. The OpenVINO argfile fixture
is retained only as an unmanaged-input negative control, not a coverage claim.

Original 3.5/3.6-fixture checks passed:

- configuration validation;
- full digest-refresh output across 149 normalized Dockerfiles and an unchanged
  OpenVINO argfile negative control;
- four unquoted/double-quoted ARG/platform/CRLF cases;
- 360 default/derived image-and-branch combinations across all five branches;
- 185 native Dockerfile digest replacements, including sequential updates;
- retained inherited update-type behavior and rejection of migration-only
  input forms without custom fallback managers.

### Additional branch coverage

A subsequent [pinned source inventory](ubi98-additional-branch-inventory.md)
adds `main`, `rhoai-2.25`, and `rhoai-3.4`: 283 matching Dockerfiles were captured,
with 236 in the demonstrated default/local-derived policy subset. The native
harness passed complete-output checks for those 236 files, including unchanged
controls, using each captured branch; it performed 224 digest replacements plus
four additional ARG/platform/CRLF cases. Configuration validation and the 360
local default/derived scope combinations passed again.

This is a Dockerfile-source inventory, not a PipelineRun/build-override audit.
The report distinguishes missing branches, unauthenticated capture gaps, and
external-preset repositories that were inventoried but not policy-validated.
The older 3.5/3.6 snapshots were not refreshed in this run.

Before adoption, confirm migration completion and run a production-equivalent
dry-run to verify resolved presets, registry lookup, file discovery, no
competing PRs, and generated changes. There is still no new multi-repository
live run or all-architecture/application compliance verification. The
pre-existing RPM compatibility warning and runner limitation remain outside
scope. Local evidence harnesses are not committed tests.
