# Rolling out to a team (and trusting what it produces)

What has to be true before a team relies on it, and before the migrations it helps write reach production.

## What changes before day one

**1. Distribute it as a pinned plugin, not a copy-paste.**
Publish the marketplace from an internal repo, and enable the plugin in each service repo's `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`). Or enforce it org-wide through managed settings with `strictKnownMarketplaces`. Pin `version` and roll forward deliberately. Run two channels: `stable` for everyone, `next` for 2–3 volunteers. A bad skill version must not reach everyone at once.

**2. Make CI the gate, not the laptop.**
The local skill is advisory: an engineer can skip it, and hooks can be bypassed outside Claude Code. `sg ci` runs in CI through the reusable workflow [`.github/workflows/gate.yml`](../.github/workflows/gate.yml) (`sg init` writes the caller), with **no LLM** in the loop for the gate itself. It only checks services whose migrations changed. Branch protection requires it:
- REFUSED fails the job, always.
- NEEDS-HUMAN fails until someone with write access adds the `schema-guard:approved` label. Combine it with "Require review from Code Owners" on migration directories and `schema-guard.yaml`, so the approval comes from the owners the card names.
- GO still requires normal review. GO means "no known risk", not "no review".

**3. Put the org's knowledge in the policy, owned by humans.**
`schema-guard.yaml` (table classes, sizes, money columns, invariants) and `LEARNINGS.md` are CODEOWNED by the data-platform team. The skill proposes additions and never commits them. Checks always run with the base branch's policy, so a PR that edits the policy is judged by the old one and needs its owners' approval. Org-wide rules live in one file that repos `extends:`; keys it lists under `locked:` can't be overridden. Sizes should be refreshed from production statistics by a scheduled job, not typed by hand (to build).

**4. Realistic data for the invariant check.**
The seed file is the weakest link. Replace it with a nightly, anonymised, sampled snapshot (for example 1% of ledger rows, with totals preserved), refreshed automatically. Otherwise invariants prove little.

**5. Run the evals as a regression suite for the skill itself.**
Any change to SKILL.md, the rules or the model version runs `evals/run_evals.py --runs 3` in CI. Gate the change on accuracy and consistency not dropping. Every production incident or wrong verdict becomes a new case. The free `--baseline-only` run already runs on every PR here.

**6. Pin the model and budget it.**
Pin the model in CI (`--model`) and set a max cost per run. Log `total_cost_usd` and turns per invocation. Watch the p95, not the mean.

## What I'd measure in the first month

- **Adoption:** plugin loaded / skill invoked per engineer per week (OpenTelemetry events from Claude Code).
- **Verdict mix:** % GO / NEEDS-HUMAN / REFUSED. If NEEDS-HUMAN exceeds ~40%, the tool is asking too much and people will route around it. Tune the policy, not the prompt.
- **Overrides:** how often a human merges over a NEEDS-HUMAN, and why. Each override feeds a rule change or a LEARNINGS entry.
- **Escapes:** migration-related incidents that got a GO. These are the number that matters. Each one becomes an eval case and a rule.
- **Time to merge** for migration PRs, before vs after.

## What I would not do on day one

- Let the skill run migrations anywhere except throwaway databases.
- Give it production or replica credentials. Row estimates come from the policy file, refreshed by a separate, audited job.
- Let GO auto-merge.
- Add a vector database or a custom MCP server. The context it needs is small and structured: policy, learnings, code search. Add infrastructure only when evals show a context gap.

## Known risks at team scale

| Risk | Mitigation |
|---|---|
| People stop reading NEEDS-HUMAN cards (alert fatigue) | Track the verdict mix; tune `checks.squawk.escalate` and the rule severities; keep the card short |
| Policy file drifts from reality (new tables unclassified) | Unknown tables are treated as "size unknown" → ASK. To build: a weekly job that lists unclassified tables |
| Prompt injection via repo content | Rule 4 in SKILL.md, plus a deterministic gate the model cannot lower. There's an eval case for it |
| The model writes a plausible but semantically wrong migration | Ask-first policy on units, backfills and NULL meaning; the verifier subagent; human review remains mandatory |
| Tool outage blocks deploys | The CI gate is deterministic and local (no API). Only the authoring assistant needs the LLM |
| A PR loosens the policy it's judged by | Checks use the base branch's `schema-guard.yaml`; a policy edit is flagged `policy-changed` and needs owner approval |
| Callers in other repos break silently | Not covered by the text scan. To build: a callers scan over the org's code-search index; until then, list consumers in LEARNINGS.md |
