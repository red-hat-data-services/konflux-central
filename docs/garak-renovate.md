# Garak updates and base pin migration

Garak uses `renovate/garak-renovate.json` instead of the shared Dockerfile-only preset.
The sync target is `.github/renovate.json` in the downstream Garak repository.

The policy enables automatic merges for Renovate updates, including major versions.
Renovate waits for successful reported checks and performs the merge itself.
GitHub-native auto-merge is disabled for this policy.

The policy preserves MintMaker's default managers and branch configuration.
It does not change other components, approval requirements, or the team-managed Jira gate.

The Garak Dockerfile contains the base image reference in a defaulted `BASE_IMAGE` argument.
The stage argument preserves the base reference in the image label.
The build also tests PyArrow and provider imports after dependency installation.

The component PipelineRun no longer supplies `build-args-file`.
The downstream repository retains an empty compatibility file for older PipelineRuns during the transition.

## Rollout

1. Merge the Garak validation workflow PR.
2. Deploy the checks to the selected release branches.
3. Ask an administrator to require the intended CI and Konflux checks.
4. Make sure that the organization approval policy permits the intended bot behavior.
5. Merge the Garak Dockerfile and Renovate policy PR.
6. Merge this central configuration and PipelineRun update.
7. Make sure that the synchronized Garak PipelineRun no longer supplies the argument file.
8. Remove the downstream compatibility file after no active pipeline references it.

Do not merge the PipelineRun change before the Dockerfile has a default base pin.
The old Dockerfile requires an external `BASE_IMAGE` value and cannot build without it.

## Configuration sync

Select `garak-renovate.json` for a Garak-only Renovate configuration sync.
The `all` selection also includes Garak.

The central policy and the downstream policy initially contain identical JSON.
The policy has no forward reference to a central preset that does not yet exist.

## Validation

Run the policy tests:

```bash
python3 -m unittest discover -s script -p 'test_garak_renovate_config.py' -v
```

Generate a Garak run matrix:

```bash
python3 script/generate-renovate-matrix.py \
  --repository llama-stack-provider-trustyai-garak
```

If a release branch needs a manual Renovate run, supply its name with `--branches`.

```bash
python3 script/generate-renovate-matrix.py \
  --repository llama-stack-provider-trustyai-garak \
  --branches rhoai-3.5
```

Frozen release branches require separate PipelineRun and Dockerfile backports.
Each backport retains the approved base image for its release stream.
