# NeMo Guardrails Python updates

NeMo-Guardrails uses `renovate/nemo-guardrails-renovate.json5` from
`konflux-central@rhoai-3.6`. Its shared central presets explicitly reference
`#rhoai-3.6` so they resolve from the same release branch.
The policy extends the shared `uv pip-compile` preset and narrows it to the
root runtime lockfile:

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

1. Merge this policy and its shared presets into `konflux-central@rhoai-3.6`.
2. Run **Sync Renovate Configuration** with **Use workflow from: rhoai-3.6**
   and `renovate-config: nemo-guardrails-renovate.json5`. First use dry-run
   mode, then sync the policy to `NeMo-Guardrails@main`.
3. Merge the NeMo `rhoai-3.6` lock-file header normalization.
4. Add the post-upgrade command to MintMaker's global allowlist if it is not
   already permitted.
5. Run the existing **Run Renovate** workflow from `konflux-central@main` in
   dry-run mode with repository `NeMo-Guardrails` and branches `rhoai-3.6`.
   That runner reads the policy installed in the target repository; the policy
   resolves its central presets from `rhoai-3.6`.
6. Review the resulting source and lock-file diff before enabling a live run.

The release branch includes a NeMo-only `config.yaml`, the sync workflow, and
its `script/generate-effective-config.py` helper. The sync still installs the
policy on the target repository's default branch, where Renovate discovers it;
the policy restricts Python updates to the NeMo release branch.

The central `main` branch still has its own NeMo sync mapping. Running that
sync for NeMo (including an `all` sync) can replace this release policy. Use
the `rhoai-3.6` sync to install or restore this policy.
