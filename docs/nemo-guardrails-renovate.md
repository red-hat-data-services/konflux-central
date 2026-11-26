# NeMo Guardrails Python updates

NeMo-Guardrails uses `renovate/nemo-guardrails-renovate.json5` from
`konflux-central@rhoai-3.6`. Its shared central presets explicitly reference
`#rhoai-3.6` so they resolve from the same release branch.
The policy extends the shared `uv pip-compile` preset and narrows it to the
root runtime lockfile using manager-scoped `includePaths`. Renovate merges
inherited `managerFilePatterns` additively, so adding a narrower pattern alone
does not exclude other lockfiles:

- Python updates run only on `rhoai-3.6`.
- Only `requirements.txt` is managed.
- Lock-file maintenance is disabled.
- Updates are merged manually after checks pass.

The `rhoai-3.6` lock file must record a parser-compatible `uv pip compile`
command whose output file is `requirements.txt`. Unsupported resolver flags
belong in `[tool.uv.pip]` so the header remains replayable by Renovate.
The command includes `dependencies/cve-constraints.txt`, which supplies security
floors for transitive dependencies. Renovate updates indirect dependencies in
the generated lockfile without changing `pyproject.toml`. A post-upgrade command
persists vulnerability-driven indirect fixes in the constraints file and reruns
the recorded compile command. Direct dependency updates remain native
Renovate updates and do not refresh `uv.lock`.

MintMaker must allow this exact repository command before the persistence step
can execute:

```text
python3 scripts/persist-security-constraints.py
```

## Rollout

Do not install the policy or enable live Python updates until the MintMaker
operator has supplied the deployment evidence below. A local extraction run
does not execute the post-upgrade command or establish deployment readiness.

1. Validate the candidate policy against the NeMo counterpart in
   [NeMo-Guardrails#479](https://github.com/red-hat-data-services/NeMo-Guardrails/pull/479).
   Capture an extraction dry run showing that pip-compile selects exactly
   `requirements.txt`, with a nonzero dependency count. Include root development
   and nested requirements files in the check.
2. Have the MintMaker operator confirm the **effective deployed** runner
   `allowedCommands` permits the exact command above. If needed, append
   `^python3 scripts/persist-security-constraints[.]py$` to that allowlist,
   preserving its existing entries. The operator can grant this permission
   specifically for NeMo in the runner configuration. It must not be added to
   the repository policy. A MintMaker configuration PR must be deployed before
   this prerequisite is satisfied.
3. Merge the NeMo counterpart and this policy with its shared presets into
   their respective `rhoai-3.6` branches. Coordinate a companion change against
   central `main` removing NeMo from `config.yaml` before installing the policy.
4. Have the operator run a targeted full dry run with the candidate policy and
   NeMo counterpart on `rhoai-3.6`, using the deployed MintMaker image and
   effective global configuration. Capture the image digest/version, source
   revisions, file selection, command execution result, and resulting
   `dependencies/cve-constraints.txt` and `requirements.txt` diff. Use an
   indirect vulnerability update that actually triggers the persistence task;
   a run with no updates does not verify that task.
5. Run **Sync Renovate Configuration** with **Use workflow from: rhoai-3.6**
   and `renovate-config: nemo-guardrails-renovate.json5`. First inspect the sync
   dry-run diff, then sync the policy to `NeMo-Guardrails@main` once the preceding
   checks pass. A sync dry run only previews copying the configuration; it is
   not a Renovate dry run.

The release branch includes a NeMo-only `config.yaml`, the sync workflow, and
its `script/generate-effective-config.py` helper. The sync still installs the
policy on the target repository's default branch, where Renovate discovers it;
the policy restricts Python updates to the NeMo release branch.

The release branch is the single sync owner. The companion change must remove
the `red-hat-data-services/NeMo-Guardrails` entry from central `main`'s
`renovate/aipcc-base-images-renovate-distribution.json` group. Until that change
is merged, a `main` sync for NeMo (including `all`) can overwrite this policy.
Do not restore the overlapping mapping for recovery; use the release sync.

The `main` **Run Renovate** workflow also builds its repository matrix from
`config.yaml`. Once the companion change lands, it cannot select NeMo. Use a
targeted MintMaker operator run for the deployment check instead.

## Deployment verification status

The deployed allowlist and full dry run are **unverified**. Cluster operator
confirmation is required; repository access alone cannot establish either.
The [upstream MintMaker allowlist at commit 2c4811a](https://github.com/konflux-ci/mintmaker/blob/2c4811ab4e278c15108ca8882497f1cdb6b8f9cb/config/renovate/renovate.json)
does not permit the NeMo persistence command. A deployment may override that
configuration, so this source check is not a substitute for deployed evidence.

## Extraction evidence

On 2026-11-26, upstream Renovate `44.209.0` selected exactly `requirements.txt`
and extracted 208 dependency records from NeMo counterpart revision
`b71ed0dceeeb008fc43cf2e0e1515e64d2f4d74a`. The check used the Python settings
from central revision `da69ec67ad2bc809da19690327f440c91fc8e52b` and its shared
Python preset, resolved through the PR fork because the release presets are
not yet merged.

The fixture includes valid `requirements-dev.txt` and `nested/requirements.txt`
decoys. With `includePaths` removed, the control run matched all three lockfiles;
with the policy unchanged, it matched only the root runtime lockfile.
The [recorded summary](nemo-guardrails-renovate-extraction.json) includes both
results and warnings. The run used the JavaScript regular-expression fallback
because native RE2 was unavailable, and reported dependencies from the source
manifest that are absent from the runtime lockfile.

Reproduce with Renovate `44.209.0`, its supported Node runtime, a checkout of the
NeMo counterpart at the revision above, and `RENOVATE_TOKEN` set for preset
resolution if needed:

```sh
python3 script/check-nemo-renovate-extraction.py \
  --source /path/to/NeMo-Guardrails \
  --output /tmp/nemo-extraction \
  --preset-repository m-misiura/konflux-central \
  --preset-ref da69ec67ad2bc809da19690327f440c91fc8e52b
```

The script writes `restricted.log`, `control.log`, and `summary.json`. It copies
the NeMo inputs into a temporary fixture and leaves the source checkout intact.
Use `--renovate` to select an executable; optionally supply `--node` when that
path is a JavaScript entry point. After merging the presets, omit the two preset
arguments to resolve them from central `rhoai-3.6`.

This check isolates the Python policy because standard Renovate does not
support MintMaker's custom RPM managers. It verifies file selection and header
parsing, not the full inherited policy, branch gating, dependency updates,
post-upgrade execution, or the deployed MintMaker configuration.
