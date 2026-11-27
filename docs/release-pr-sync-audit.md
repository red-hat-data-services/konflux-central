# Release PR sync audit

These are **pinned candidate-state results**, not approval to enable live sync.
One-time cleanup is tracked separately in
[RHAI-19583](https://redhat.atlassian.net/browse/RHAI-19583). Six older-release
repository/release blockers remain; their detailed diagnostics are preserved
in the local evidence listed below.

## Latest candidate results

The audit uses each release's own managed-repository inventory and KC push
configuration. Existing cleanup candidate revisions and the approved local
`open-data-hub-tenant` removals are applied in disposable copies. Generation
uses the implementation branches, not the published shared
`validate-tekton-dev` or KC main revisions. Shared PR #41 must land on
`validate-tekton-dev` before the updated consumer checkout can generate children. All 281 component release inputs were available.

| Release | Managed repositories | Validated | Blocked | Validated push/PR pairs |
| --- | ---: | ---: | ---: | ---: |
| `rhoai-2.25` | 41 | 41 | 0 | 73 |
| `rhoai-3.4` | 50 | 47 | 3 | 94 |
| `rhoai-3.5` | 56 | 53 | 3 | 113 |
| `rhoai-3.6` | 67 | 67 | 0 | 146 |
| `rhoai-3.7-ea.1` | 67 | 67 | 0 | 146 |

For every successful repository, preparation ran actual validator fix,
independent non-fix validation from disk, and another fix for idempotence.
KC non-PR inputs and remaining separately owned PRs were verified byte-identical.
Generated names reserve ten PR-number digits within Tekton's 63-character limit.
Successful candidates contained no explicit ODH-tenant resource. Failed cases
are not counted as coverage, and parsing failures may conceal further blockers.

The full shared regression suite passed 878 tests, and KC offline preparation /
name-image contract regressions passed 129 tests. These tests are separate from
Kubernetes admission, real image builds, cluster identity checks, and live sync.

## Pinned KC inputs

| Release | Source revision |
| --- | --- |
| `rhoai-2.25` | `6a09f5fd45c2b7c2efcfc6b818bf80a4d72259af` |
| `rhoai-3.4` | `43e48efe20357252cb034d48d8e86689521e5e57` |
| `rhoai-3.5` | `850b96e16c6c2273e21c9d64d82f445fa79c0410` |
| `rhoai-3.6` | `01c247c310d95d3ab7eb689d0630e7a02894713c` |
| `rhoai-3.7-ea.1` | `c678273349f8d0d5a964348b8d5b5312a4c60a0e` |

The newer source revisions include the one-time OpenVINO source cleanup. Their
parents were `a1e74e4a7406bf7e3d9a7454dbdfdd8b806d07dd` and
`24d7d6f6bb9bf9876e6f6286aafd5cc4fffc92ed`. Component revisions and earlier
cleanup candidates are recorded individually in the local manifests/results.
Refresh all bases and repeat validation before publication; no claim is made
that these snapshots equal current remote release HEADs.

## Important interpretation

Earlier audits found overlong names, ambiguous matches, legacy resource streams,
and orphan PRs. The implementation now bounds names deterministically, matches
shortened names through unchanged component labels, accepts valid resource-only
YAML streams, and recognizes the exact legacy target-branch placeholder.
Duplicate keys, bundled PipelineRuns, unsupported parents, ambiguity, collisions,
and unclassified PRs remain fail-closed.

The ownership decision changed during review: explicit ODH-tenant definitions
are to be removed through one-time component PRs, not preserved. Seven obsolete
preservation entries were removed accordingly. Remaining downstream E2E/legacy
exceptions assert exact application, component, pipeline URL, image repository,
Dockerfile, and build context. Their preservation is not correctness certification;
in particular, an existing legacy ROCm PR references a CUDA context/component
and was not repaired by this implementation.

No persistent retirement/deletion mechanism exists in sync preparation. Historical
experimental deletion-policy results are not acceptance evidence. The latest
coverage depends on the separate component cleanup candidates, including the
111 newly prepared local tenant-removal worktrees. Those staged component
patches have not been committed or published. RHOAI 3.6 cleanup is no longer
GA-gated. No live sync or builds were manually dispatched during these audits.

## Evidence locations

Local-only, temporary evidence:

- `/tmp/odh-tenant-cleanup.iusuugji/manifest.json`: 111 deletion-only worktrees,
  339 exact file removals, base SHAs, and scheduling metadata.
- `/tmp/five-release-tenant-cleanup-audit.a2mxqv9g/`: latest per-repository
  fix, validation, idempotence, ownership, result, and tooling-fingerprint files.
- `/tmp/five-release-compat-audit.7fxnx04h/`: pre-tenant-cleanup compatibility audit.
- `/tmp/older-release-sync-audit.y55zn0k7/`: older-release inputs and inventories.
- `/tmp/release-cleanup-candidate-audit.0h3lbss1/`: earlier two-release cleanup audit.
- `/tmp/release-sync-audit.2Rqt3l/`: initial inventories and historical diagnostics.

Retain exact inputs and raw reports in durable artifacts before component PR
publication. Updating KC main does not update release workflows; propagation,
dry-run review, and live publication each remain separate rollout gates.
