# Renovate Config Layering: What We Tried

Notes on the various approaches attempted to replicate MintMaker's
two-layer config stack (global admin config + repo config) in our
on-demand Renovate workflow. Documented here for future reference
in case Renovate's behavior changes or we need to revisit.

## The Problem

MintMaker (production Renovate) applies two config layers:
1. **Global admin config** — sets `enabledManagers` (~60 managers),
   `groupName`, `postUpgradeTasks`, `branchPrefix`, etc.
2. **Repo config** — our source config (e.g., `pipelines-renovate.json5`)
   overrides `enabledManagers` to just `["tekton", "custom.regex"]`

Our on-demand workflow needs to replicate this. The challenge is that
several Renovate config mechanisms behave unexpectedly when used outside
the production MintMaker context.

## What Worked

**`RENOVATE_CONFIG_FILE` + `RENOVATE_EXTENDS` + `RENOVATE_FORCE`**

- `RENOVATE_CONFIG_FILE` — our source config, mounted directly into the
  container. Renovate auto-migrates deprecated fields (e.g., `fileMatch`
  to `managerFilePatterns`).
- `RENOVATE_EXTENDS` — MintMaker's global config as the base. Provides
  `groupName`, `postUpgradeTasks`, `branchPrefix`, replacement rules.
- `RENOVATE_FORCE` — overrides only `enabledManagers` and
  `baseBranchPatterns`. Force has the highest priority in Renovate and
  cannot be overridden by any other config layer.
- `RENOVATE_REQUIRE_CONFIG=ignored` — prevents the repo's own
  `.github/renovate.json` from loading (it would resolve `extends` from
  the default branch, not the feature branch).

## What Didn't Work

### 1. Inline source config + extends to MintMaker in `RENOVATE_CONFIG_FILE`

**Approach:** Read the source config locally, prepend MintMaker's
`extends` reference, write as a wrapper config file.

```json
{
  "extends": ["github>konflux-ci/mintmaker//config/renovate/renovate.json"],
  "enabledManagers": ["tekton", "custom.regex"],
  ...rest of source config...
}
```

**Why it failed:** `extends` in `RENOVATE_CONFIG_FILE` are resolved at
the repo level as an overlay that overrides the config file's own inline
fields. MintMaker's `enabledManagers` (~60 managers) replaced our
`["tekton", "custom.regex"]` even though inline fields should have
higher priority than extends. This also affected `baseBranches`.

### 2. Pure-extends wrapper with `github>` reference at a commit SHA

**Approach:** A wrapper config with only extends — MintMaker first, our
source config second (referenced via `github>...#sha`). No inline fields.

```json
{
  "extends": [
    "github>konflux-ci/mintmaker//config/renovate/renovate.json",
    "github>red-hat-data-services/konflux-central//renovate/pipelines-renovate.json5#abc123"
  ]
}
```

**Why it failed:** `enabledManagers` is unmergeable, so our config (last
in extends) correctly replaced MintMaker's. But `baseBranches` from our
config also came through extends, overriding any inline or env var
values. Adding `baseBranches` inline to the wrapper didn't help — extends
override inline fields. Additionally, `github.sha` in `workflow_dispatch`
events points to the default branch HEAD, not the feature branch.

### 3. `RENOVATE_ENABLED_MANAGERS` env var

**Approach:** Set `enabledManagers` via environment variable, which
should have the highest priority.

**Why it failed:** Env vars DO override config file inline fields, but
they do NOT override values from `extends` presets that explicitly set
the same field. Since MintMaker's extends sets `enabledManagers`, the
env var was ignored. The same issue applied to `RENOVATE_BASE_BRANCHES`
when `baseBranches` came through extends.

Note: env vars DO work for fields that the extends don't set. For
example, `RENOVATE_BASE_BRANCHES` worked when MintMaker was the only
extends source (MintMaker doesn't set `baseBranches`).

### 4. Full source config in `RENOVATE_FORCE`

**Approach:** Put MintMaker in `RENOVATE_EXTENDS`, put our entire source
config in `RENOVATE_FORCE`.

**Why it partially failed:** Force overrides everything, so
`enabledManagers` and `baseBranches` worked correctly. But force also
overrides MintMaker's settings we WANT to inherit. For nested objects
like `tekton`, our force config replaced MintMaker's entire `tekton`
section — losing `groupName`, `postUpgradeTasks`, and other settings.
This required duplicating all of MintMaker's tekton settings in our
source config, which defeats the purpose of inheriting from MintMaker.

### 5. `RENOVATE_REQUIRE_CONFIG=optional` (let repo config load)

**Approach:** Let the repo's `.github/renovate.json` load naturally,
providing our source config at the repo level (higher priority than
the global admin config).

**Why it partially failed:** Repo config correctly overrode MintMaker's
global config for most fields. But the repo config resolves `extends`
references via `github>` from the default branch (main), so feature
branch config changes weren't picked up. Also, `baseBranches` from the
repo config overrode the `RENOVATE_FORCE` value — force should have
the highest priority, but `baseBranches` needed to use the migrated
field name `baseBranchPatterns` (force bypasses config migration).

## Key Takeaways

- **`extends` in `RENOVATE_CONFIG_FILE` override inline fields.** This
  is counterintuitive — normally extends are the base and inline fields
  override them. But for config files loaded via `RENOVATE_CONFIG_FILE`,
  resolved extends values take priority over the file's own fields.

- **Env vars cannot override extends-resolved fields.** If an extends
  preset explicitly sets a field, the env var for that field is ignored.
  Env vars only work for fields not set by extends.

- **`RENOVATE_FORCE` overrides everything but replaces entire objects.**
  Force is the only reliable way to override extends-resolved fields.
  But for nested objects (like `tekton`), it replaces the entire object
  rather than merging, so you lose inherited settings.

- **`RENOVATE_FORCE` bypasses config migration.** Deprecated field names
  (e.g., `baseBranches` vs `baseBranchPatterns`, `fileMatch` vs
  `managerFilePatterns`) must use the current names in force configs.
  `RENOVATE_CONFIG_FILE` auto-migrates, but force does not.

- **`github>` in extends always resolves from the default branch.** This
  makes feature branch testing impossible via extends references. Mount
  the local file via `RENOVATE_CONFIG_FILE` instead.
