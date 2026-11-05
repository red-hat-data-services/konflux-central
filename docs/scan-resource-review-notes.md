## Scan-resource review before merging

Only scan-step CPU/memory policy is automated. Task-level budgets remain manual; unresolved external/dynamic Pipeline references are not fetched or guessed. Review the generated summary's deferred paths and reasons; a successful preview is not complete branch alignment.

Verify the admitted scan pod has the intended memory/CPU requests and **no CPU ceiling**, including bundled defaults and admission behavior. Removing a field from YAML does not prove the admitted limit is absent. Review scan outcome/duration, scheduling, and quota impact. The **512Mi baseline is provisional**.

Do not auto-merge. No manual Konflux rerun or deployment is requested; normal PR CI may run.

Related: [RHAI-19093](https://redhat.atlassian.net/browse/RHAI-19093).
