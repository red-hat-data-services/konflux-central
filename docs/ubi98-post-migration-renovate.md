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
| `.konflux/build-args.conf` base-image assignments | Custom `ubi98-argfile` |

The argfile custom manager accepts only `:9.8@sha256:...` references. It uses the
actual captured version: no synthetic versions, latest-to-9.8 conversion,
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

For the same twelve approved image repositories, supported files, and branches
(`main`, `rhoai-3.6`, `rhoai-3.5`), references already on 9.8 receive:

- `allowedVersions: /^9\.8$/` and digest pinning;
- no automerge and no ignoring CI failures;
- ordinary digest refreshes rather than migration updates.

The constraint matches an existing 9.8 version. Explicit other numbered tags
retain ordinary behavior; new UBI 10 repository names are outside the exact
image allowlist. This is not a mechanism to repair every future missing pin.
Existing manager update-type settings and derived-preset overrides are
preserved; this change does not introduce new version-bump enable rules.

Custom extraction is disabled by default and enabled only for the selected
images, branches, and file scope. OpenJDK remains excluded. There are no direct
EA updates. Existing default-distribution mappings are unchanged.

## Validation and merge prerequisites

Offline checks used installed Renovate **43.210.0-rpm**, not workflow version
44.71.0-rpm. Synthetic already-migrated fixtures were generated from the 150
pinned source snapshots using the migration renderer; placeholder digests are
not registry evidence or real component changes.

Checks passed:

- configuration validation;
- full digest-refresh output across all 150 normalized source files;
- four unquoted/double-quoted ARG/platform/CRLF cases;
- 216 default/derived image-and-branch combinations;
- 187 native/custom digest replacements, including sequential updates;
- retained inherited update-type behavior and rejection of migration-only
  input forms by the custom managers.

Before adoption, confirm migration completion and run a production-equivalent
dry-run to verify resolved presets, registry lookup, file discovery, no
competing PRs, and generated changes. There is no refreshed main inventory,
new multi-repository live run, or all-architecture/application compliance
verification. The pre-existing RPM compatibility warning and runner limitation
remain outside scope. Local evidence harnesses are not committed tests.
