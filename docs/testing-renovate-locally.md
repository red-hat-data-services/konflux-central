# Testing Renovate Locally

How to test Renovate configuration changes using the official container image
before relying on MintMaker (the hosted Renovate instance in Konflux).

All commands below clone the repo from GitHub — Renovate never reads your local
working directory.

## Prerequisites

- Podman (or Docker)
- A GitHub personal access token with repo scope (`gh auth token` works)

## 1. Dry-Run Current Config

Validates that the config on the remote default branch (`main`) can extract
dependencies and find updates, without creating any branches or PRs.

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_DRY_RUN=lookup \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 | tee /tmp/renovate-debug.log
```

### What to Look For

- **Dependency extraction**: `grep 'No dependencies found' /tmp/renovate-debug.log`
  should only appear for files that genuinely have no matches.
- **Validation failures** (TRACE level): `grep 'Discarding' /tmp/renovate-debug.log`
  shows dependencies that matched the regex but failed validation (missing
  `datasource`, `currentValue`, etc.).
- **Updates found**: `grep 'flattened updates found' /tmp/renovate-debug.log`
  confirms Renovate detected version drift.
- **Config errors**: `grep 'Possible config error' /tmp/renovate-debug.log`

## 2. Dry-Run Proposed Changes

When iterating on a config change that hasn't been merged yet, you need
Renovate to pick up the config from your feature branch or from env var
overrides.

### Option A: Push your branch first

Push your branch, then use `RENOVATE_BASE_BRANCHES` and
`RENOVATE_USE_BASE_BRANCH_CONFIG=merge` to tell Renovate to read the config
from that branch:

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e RENOVATE_DRY_RUN=full \
  -e RENOVATE_REPOSITORIES='["red-hat-data-services/konflux-central"]' \
  -e RENOVATE_BASE_BRANCHES='["my-feature-branch"]' \
  -e RENOVATE_USE_BASE_BRANCH_CONFIG=merge \
  -e RENOVATE_REQUIRE_CONFIG=optional \
  -e LOG_LEVEL=debug \
  ghcr.io/renovatebot/renovate:latest \
  2>&1 | tee /tmp/renovate-test.log
```

- `RENOVATE_DRY_RUN=full` simulates the full run (branches, PRs, automerge)
  without actually writing anything.
- `RENOVATE_BASE_BRANCHES` overrides the `baseBranches` in the config, so
  Renovate only processes your feature branch instead of all release branches.
- `RENOVATE_USE_BASE_BRANCH_CONFIG=merge` tells Renovate to read
  `.github/renovate.json` (and its extends) from the target branch rather than
  the default branch.

### Option B: Mount the local config file

Since `.github/renovate.json` is just a pointer that extends a config file
from the `renovate/` directory, you can edit that file locally, mount it into
the container, and test without pushing anything. Substitute the path to
whichever config file you're changing (e.g. `pipelines-renovate.json5`,
`default-renovate.json5`, etc.):

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -v "$(pwd)/renovate/<your-config-file>.json5:/tmp/config.json5:ro" \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e RENOVATE_DRY_RUN=full \
  -e RENOVATE_REPOSITORIES='["red-hat-data-services/konflux-central"]' \
  -e RENOVATE_BASE_BRANCHES='["main"]' \
  -e RENOVATE_REQUIRE_CONFIG=ignored \
  -e RENOVATE_CONFIG_FILE=/tmp/config.json5 \
  -e LOG_LEVEL=debug \
  ghcr.io/renovatebot/renovate:latest \
  2>&1 | tee /tmp/renovate-test.log
```

- `-v ... :ro` mounts your local config file read-only into the container.
- `RENOVATE_CONFIG_FILE` tells Renovate to use the mounted file as its config.
- `RENOVATE_REQUIRE_CONFIG=ignored` skips the repo's `.github/renovate.json`
  so your local file is the only config source.

### What to Look For

- **Automerge resolved correctly**:
  `grep 'automerge' /tmp/renovate-test.log | grep -i 'configured\|converting'`
- **PR would be created**:
  `grep 'DRY-RUN.*Would' /tmp/renovate-test.log`
- **Package disabled**:
  `grep 'is disabled' /tmp/renovate-test.log`

## 3. Live PRs from Proposed Changes

Same as Option A above but without `RENOVATE_DRY_RUN`, so Renovate actually
creates branches and PRs using your feature branch's config. Useful for
end-to-end validation that automerge, grouping, and scheduling work as
expected before merging the config change.

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e RENOVATE_REPOSITORIES='["red-hat-data-services/konflux-central"]' \
  -e RENOVATE_BASE_BRANCHES='["my-feature-branch"]' \
  -e RENOVATE_USE_BASE_BRANCH_CONFIG=merge \
  -e RENOVATE_REQUIRE_CONFIG=optional \
  -e RENOVATE_GIT_AUTHOR="Your Name <you@example.com>" \
  -e LOG_LEVEL=debug \
  ghcr.io/renovatebot/renovate:latest \
  2>&1 | tee /tmp/renovate-pr.log
```

Check results:

```bash
grep -iE 'PR created|result.*pr-created' /tmp/renovate-pr.log
```

## 4. After Merging: Dry-Run and Live PRs

Once a config change has landed on `main`, you can run Renovate immediately
instead of waiting for MintMaker's next scheduled run (every 4 hours).

### Dry-run to verify

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e RENOVATE_DRY_RUN=full \
  -e LOG_LEVEL=debug \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 | tee /tmp/renovate-post-merge.log
```

### Create real PRs

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_GIT_AUTHOR="Your Name <you@example.com>" \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 > /tmp/renovate-pr.log
```

### Rate Limits

Renovate defaults to `prHourlyLimit: 2` and `branchConcurrentLimit: 10`. If
you need to create many PRs in one run, raise these limits:

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_GIT_AUTHOR="Your Name <you@example.com>" \
  -e RENOVATE_PR_HOURLY_LIMIT=20 \
  -e RENOVATE_BRANCH_CONCURRENT_LIMIT=20 \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 > /tmp/renovate-pr.log
```

## Useful Grep Patterns

| What | Command |
|------|---------|
| Extraction results | `grep 'Dependency extraction complete' log` |
| Custom regex matches | `grep 'No dependencies found in file for custom regex' log` |
| Update proposals | `grep 'flattened updates found' log` |
| Branch processing | `grep 'processBranch\|result.*pr-created\|branch-limit' log` |
| Rate limits hit | `grep 'Reached.*limit' log` |
| Push errors | `grep 'error.*push\|Unknown error committing' log` |
