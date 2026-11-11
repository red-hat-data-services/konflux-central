# Default-policy UBI 9.8 updates

The default Dockerfile/RPM policy migrates selected UBI image references to a
version tag plus digest and then tracks the selected tag. This expands the
original Praxis PQC pilot to the 13 same-registry image repositories identified
in the release inventory. It applies to `main`, `rhoai-3.6`, and `rhoai-3.5`.
The shared branch scan list is unchanged.

During this development cycle, `rhoai-3.7-ea.1` receives changes through sync
from `main`. Direct MintMaker updates to EA would risk competing changes and
sync conflicts, so EA is not a direct policy target.

## Approved images and tags

These exact image repositories track `:9.8@sha256:...`:

- `registry.access.redhat.com/ubi9-minimal`
- `registry.access.redhat.com/ubi9/nodejs-22-minimal`
- `registry.access.redhat.com/ubi9/nodejs-22`
- `registry.access.redhat.com/ubi9/python-312-minimal`
- `registry.access.redhat.com/ubi9/python-312`
- `registry.access.redhat.com/ubi9/ubi-minimal`
- `registry.access.redhat.com/ubi9/ubi`
- `registry.redhat.io/ubi9-minimal`
- `registry.redhat.io/ubi9/python-312`
- `registry.redhat.io/ubi9/ubi-minimal-pqc`
- `registry.redhat.io/ubi9/ubi-minimal`
- `registry.redhat.io/ubi9/ubi`

`registry.redhat.io/ubi9/openjdk-17` has no literal `:9.8` tag. It instead uses
`:1.24-3.1793588566@sha256:...`, the verified version-release tag whose metadata
identifies a UBI 9.8 base. Its allowed version is that exact tag, not a floating
OpenJDK channel. Selecting a newer OpenJDK build requires separate verification
and a policy change.

Registry hostnames and repository paths are preserved. The selected tag's
current digest is authoritative: a 9.9 digest is replaced with the selected
9.8 digest, and an older 9.8 digest can also refresh. This is not a policy of
preserving every existing 9.8 digest. Tags remain mutable; the digest pins the
build contents.

## Extraction and migration

The custom managers handle digest-only, `latest`, `latest@digest`, and already
compliant selected-tag references. They include implicit latest on literal
`FROM` lines and add a missing digest to a selected-tag reference.
Explicit other numbered tags are not migrated.

File scope includes the original five Konflux/RHOAI Dockerfile patterns plus
`Containerfile.server.konflux`. Source forms include:

- Literal `FROM` image tokens, including optional `--platform` and aliases.
- Complete literal image values in Dockerfile `ARG` defaults. Bare repository
  ARG defaults are not rewritten independently, because callers may append a
  separate tag variable.
- Literal `BASE_IMAGE` and `RELEASE_BASE_IMAGE` assignments in
  `.konflux/build-args.conf` (the OpenVINO build input).
- The adjacent `UBI_MINIMAL_BASE_IMAGE` / `UBI_BASE_IMAGE_TAG` defaults used by
  the vLLM orchestrator gateway, preserving the variable-based `FROM` lines.
- Praxis extproc's `UBI9_MINIMAL_PQC_DIGEST` default and corresponding PQC
  `FROM` tag, updated together while retaining the digest variable and the
  intervening Dockerfile contents.

This is not a general Dockerfile/build-arg resolver. New split-variable forms,
other argfiles, pipeline overrides, or generated-source layouts need explicit
review. The managers match qualifying references in supported files, not only
final-stage references; an earlier stage can also supply the final stage's base
through inheritance. Comments, `RUN` text, unapproved images, and unrelated
numbered image streams are excluded.

A conditional `currentValueTemplate` supplies a synthetic Docker-compatible
version only when the selected tag is missing. This triggers a real version
update even when the existing digest already equals the target digest. The
replacement adds the selected tag and digest immediately. On re-extraction,
the actual selected tag is captured, so subsequent runs perform ordinary digest
refreshes rather than repeated migration updates.

## Ownership and safeguards

The common managers use recursive match clauses: select the instruction or
assignment, validate the complete registry/tag/digest token, apply a compact
image-family guard, then extract its fields. OpenJDK's single-image manager
does not need the additional family guard. This avoids repeating the full
repository list inside the regexes. The extraction guard also prevents a
downstream catch-all enable rule from expanding image scope. The exact image
allowlist, approved versions, and branch selection remain in package rules.
The custom managers retain the `ubi98` dependency type and are disabled by
default before the selected-branch enable rules.

The policy managers are enabled only for approved images on the three selected
branches. Ordinary Dockerfile/custom-regex updates for the approved images in
policy-managed files are disabled on those branches to avoid competing updates;
unrelated managers and images are not changed. The two split-variable managers
remain separate because their replacement requirements differ.

Policy updates do not automerge and do not ignore CI failures. Existing FIPS
and `check-payload` checks remain required; image labels do not establish FIPS
certification. Derived presets overriding `enabledManagers` must retain
`custom.regex`; model-registry explicitly does so.

No notebooks- or Garak-specific changes are included. Their inspected AIPCC
inputs are not among these image repositories. Notebooks already inherits the
default policy indirectly through its downstream configuration; this change
does not add new inheritance. Independent presets such as Garak and OGX
**distribution** are not made to inherit the default policy. The OGX Kubernetes
operator is a separate repository already mapped to the default distribution.

The central sync mapping also adds `lm-evaluation-harness`,
`model-metadata-collection`, `models-perf-benchmark-data`,
`vllm-orchestrator-gateway`, and `workbenches` (all under
`red-hat-data-services`) to the default distribution, targeting
`.github/renovate.json`. This deliberately opts them into the whole default
preset, including its other managers, broader branch scan list, and ordinary
non-policy update behavior—not only the UBI 9.8 rules. The sync replaces that
file rather than merging existing repository settings, and a `.json` config
can take precedence over a `.json5` config.

Adding these mappings does not itself distribute the files or establish
MintMaker enrollment. The manually dispatched sync workflow must be reviewed
and run separately; its current non-dry-run implementation pushes downstream
config changes directly to `main`. No downstream sync is performed as part of
these local changes.

## Validation and limitations

Local checks use the existing Renovate installation in a container with
`--pull=never --network=none`. They cover configuration validation, repository/
branch/manager rules, local derived-preset merges, extraction and actual
replacement rendering, including pinned source snapshots from the 3.5/3.6
inventory. The readability refactor is compared against the previously
committed preset: effective dependencies and replacement output must match,
including under local derived presets. Negative cases also exercise downstream
blanket enable rules, so simplifying extraction does not silently expand image
scope. Package rules and the two split-variable managers are unchanged by the
refactor. Harnesses and logs remain local investigation artifacts, not committed
tests.

A separate offline check exercises Renovate's actual update lookup with Docker
release and digest responses stubbed in memory. It verifies same-digest
migration, compliant no-ops, digest refresh, and missing-digest pinning for both
regular UBI and the OpenJDK exception. This does not validate registry responses
or resolve external presets. There is no corresponding main-branch inventory
or new authenticated multi-repository dry-run/live rollout for this expansion.

The inventory's tag verification inspected Linux/amd64 metadata, not all target
architectures, application behavior, or filesystem/package compliance. Tags can
change after observation; registry metadata does not prove `check-payload`
will pass.

The original Praxis pilot had successful authenticated dry-run/live Renovate
runs, but those do not establish correctness of the expanded policy. The
pre-existing unsupported `rpm` configuration warning appeared in local testing
and the original Actions dry-run (Renovate `44.71.0-rpm`); it is not fixed here.
The repository runner's `--config-file` limitation also remains outside scope.
