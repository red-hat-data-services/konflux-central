# NeMo Guardrails Python updates

NeMo-Guardrails uses `renovate/nemo-guardrails-renovate.json5`.
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

1. Sync the central policy to `NeMo-Guardrails@main`.
2. Merge the `rhoai-3.6` lock-file header normalization.
3. Run the on-demand Renovate workflow in dry-run mode against
   `NeMo-Guardrails` and `rhoai-3.6`.
4. Review the resulting source and lock-file diff before enabling a live run.
5. Add the post-upgrade command to MintMaker's global allowlist if it is not
   already permitted.
