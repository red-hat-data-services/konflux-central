# Testing Renovate Locally

How to test Renovate configuration changes using the official container image
before relying on the hosted Renovate GitHub App.

## Prerequisites

- Podman (or Docker)
- A GitHub personal access token with repo scope (`gh auth token` works)

## Dry-Run (Lookup Only)

Validates that Renovate can extract dependencies and find updates, without
creating any branches or PRs.

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_DRY_RUN=lookup \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central
```

Pipe the output to a file for easier searching:

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_DRY_RUN=lookup \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 > /tmp/renovate-debug.log
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

## Creating Actual PRs

Runs Renovate in full mode. It will push branches and open PRs against the
repository.

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_GIT_AUTHOR="Your Name <you@example.com>" \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central \
  2>&1 > /tmp/renovate-pr.log
```

Check results:

```bash
grep -iE 'PR created|result.*pr-created' /tmp/renovate-pr.log
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

### Overriding the Repo Config

The container reads the repo's `.github/renovate.json` by default. To test a
config change before pushing it, pass `RENOVATE_REQUIRE_CONFIG=ignored` and
supply the full config via `RENOVATE_CONFIG`:

```bash
TOKEN=$(gh auth token) && podman run --rm \
  -e RENOVATE_TOKEN="$TOKEN" \
  -e LOG_LEVEL=debug \
  -e RENOVATE_DRY_RUN=lookup \
  -e RENOVATE_REQUIRE_CONFIG=ignored \
  -e RENOVATE_CONFIG='{ ... your config JSON ... }' \
  ghcr.io/renovatebot/renovate:latest \
  red-hat-data-services/konflux-central
```

Without `RENOVATE_REQUIRE_CONFIG=ignored`, the repo config will merge on top of
whatever you pass via `RENOVATE_CONFIG`, which can mask the changes you're
trying to test.

## Useful Grep Patterns

| What | Command |
|------|---------|
| Extraction results | `grep 'Dependency extraction complete' log` |
| Custom regex matches | `grep 'No dependencies found in file for custom regex' log` |
| Update proposals | `grep 'flattened updates found' log` |
| Branch processing | `grep 'processBranch\|result.*pr-created\|branch-limit' log` |
| Rate limits hit | `grep 'Reached.*limit' log` |
| Push errors | `grep 'error.*push\|Unknown error committing' log` |
