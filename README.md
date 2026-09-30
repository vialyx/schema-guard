# schema-guard

A Claude Code plugin for safe schema changes on business-critical tables such as ledgers, money and inventory.
It writes or reviews a migration, proves it with deterministic checks, and ends with one verdict:

| Verdict | Meaning | CI exit |
|---|---|---|
| 🟢 **GO** | All checks passed and nothing is unclear. Normal review applies. | 0 |
| 🟡 **NEEDS-HUMAN** | A named owner must decide: an open question, a risky lock, unknown callers, or a check that couldn't run. | 1 |
| 🔴 **REFUSED** | Unsafe as written, e.g. FLOAT money, editing a shipped migration, rewriting ledger rows. The card gives the safe alternative. | 2 |

## Why schema changes
Schema changes are where AI-written code fails most quietly: the migration looks right in review, then locks a 40M-row table, stores money as FLOAT, breaks a job that builds SQL at runtime, or rewrites ledger history. The demo org's [LEARNINGS.md](examples/acme-mini-org/LEARNINGS.md) records three such incidents. Code review rarely catches them, because what goes wrong depends on table size, on callers outside the diff, and on business rules. schema-guard puts that context and those checks next to the engineer and into CI.

## How it works
- **The skill** does the reasoning. It investigates the tables and callers, and asks when business meaning is unclear (units, NULL meaning, backfill). It writes backward-compatible (expand/contract) migrations, plus the model, code and tests.
- **The `sg` engine** runs deterministic checks with no LLM:
  - 7 policy rules;
  - [squawk](https://squawkhq.com) lock linting;
  - a callers scan (dynamic SQL is reported as *unresolved*);
  - an up → down → up round trip on a throwaway Postgres;
  - business invariants on seed data;
  - the service's own tests.
- **The verdict rule:** the checks set the minimum verdict. The model can only make the verdict stricter, never more lenient. A skipped check or an open question never yields GO.
- **Guardrails:** a hook blocks edits to migrations that have already shipped, and a read-only verifier subagent re-checks the result.

Details: [docs/architecture.md](docs/architecture.md).

## Try it (5 minutes)
Requirements: [uv](https://docs.astral.sh/uv/), git, Node, and either PostgreSQL 16 binaries or a disposable server in `SG_DATABASE_URL`. You need `SG_DATABASE_URL` when `initdb` can't run, for example as root in a container.

```bash
git clone https://github.com/vialyx/schema-guard && cd schema-guard
bash examples/build_fixture.sh /tmp/acme && cd /tmp/acme      # demo repo with shipped migrations
claude --plugin-dir "$OLDPWD/plugins/schema-guard"
> /schema-guard:safe-schema-change add an optional inspection_notes column to orders
```

## Adopt it in your repo
```bash
alias sg='uvx --from "git+https://github.com/vialyx/schema-guard#subdirectory=plugins/schema-guard/engine" sg'
sg init      # finds your services, writes schema-guard.yaml, the CI workflow, .claude/settings.json, .gitignore
sg doctor    # checks uv, squawk, the throwaway database, the base branch and each service
```
Then:
1. **Review the TODOs in `schema-guard.yaml`:** confirm the guessed table classes (`ledger | audit | hot | core | scratch`), add `est_rows` from production, a `tests:` command and a `seed.sql` (synthetic data) per service, and invariants for money tables. The annotated [example](examples/acme-mini-org/schema-guard.yaml) shows every option.
2. **Add CODEOWNERS entries** for migration directories and `schema-guard.yaml`. The card names them as required reviewers, and changes to the policy need their approval.
3. **Commit, and make the `schema-guard` check required.** The workflow calls the reusable [gate](.github/workflows/gate.yml): no LLM, no secrets.
   - GO passes, and REFUSED always fails.
   - NEEDS-HUMAN fails until someone with write access adds the `schema-guard:approved` label.
   - PRs that don't touch migrations pass immediately.

Without `schema-guard.yaml`, `sg` finds services by itself (zero-config), but every table is then of unknown size and class, so expect more NEEDS-HUMAN.

**Frameworks:** `alembic`, and `raw_sql` (Flyway-style `V1__x.sql`, optional `U1__x.sql` undo files; forward-only is the default; set `migrations_dir:` for unusual layouts). Tests and Alembic's `env.py` run in the service's own Python: `python:` per service, otherwise `.venv`. To add a framework, see [docs/adding-a-framework.md](docs/adding-a-framework.md).

**Org-wide policy:** put shared rules (money column patterns, lint severities) in one file and reference it with `extends: https://…/schema-guard-org.yaml` (or `$SG_ORG_POLICY`). Keys the org lists under `locked:` can't be overridden by a repo.

No production credentials are needed anywhere. `sg` only ever connects to a throwaway database, and it stops if Alembic's `env.py` tries to connect anywhere else.

## Daily use
- **Write a change:** ask Claude Code for it ("add an optional notes column to orders"); the skill picks it up, or type `/schema-guard:safe-schema-change …`. Small additive changes take a fast path; ambiguous ones get questions first.
- **Review a change:** "review migration 0005". It produces a verdict card to paste into the PR, with the required reviewers.
- **Run it by hand:**

  | Command | Does |
  |---|---|
  | `sg detect` | Shows the service, framework and tools found, and each migration's shipped or pending state |
  | `sg investigate --tables a,b [--columns t.c]` | Shows DDL, table class, size and callers |
  | `sg check [--path svc] [--base ref]` | Runs all checks and writes `.schema-guard/checks.json` |
  | `sg verdict [--intent … --summary …]` | Prints the card and writes `.schema-guard/verdict.json` and `card.md` |
  | `sg ci [--base origin/main]` | The CI gate: runs check and verdict for every service whose migrations changed |
  | `sg init` / `sg doctor` | Sets up a repo / explains what's missing |

  Inside Claude Code, `sg` is the plugin's `bin/sg`.

## Example
[examples/walkthrough](examples/walkthrough/) is a recorded two-turn session:
1. **An ambiguous request:** "add grade deductions to orders". The skill wrote no migration. It asked 5 questions ([card](examples/walkthrough/card-turn1.md)) covering units, cardinality, NULL meaning, already-billed ledger rows, and a legacy job that builds SQL at runtime.
2. **After the answers:** it wrote the migrations, model, code and tests, and all checks passed. The verdict stayed **NEEDS-HUMAN** because the legacy job could now trip the new constraint, so its owners must sign off ([card](examples/walkthrough/card-turn2.md), [diff](examples/walkthrough/changes.diff)).

## Evals
There are 15 labelled cases across GO, NEEDS-HUMAN and REFUSED, including a false-positive trap and a prompt injection ([evals/cases](evals/cases/)). Each run starts from a fresh demo repo and runs headless `claude -p`.

| (Sonnet, 3 runs per case) | Correct |
|---|---|
| Deterministic checks alone | 8/15 cases |
| Skill v1 | 43/45 runs (both misses over-asked on a safe new table) |
| Skill v2: adds non-blocking `suggestions` and a test for what counts as a blocking question | Trap case 3/3; the ambiguous case still asks, 3/3 |

A run costs about $0.25 and takes 30–80 s. To reproduce: `uv run --project plugins/schema-guard/engine python evals/run_evals.py --runs 3 --model sonnet` (add `--baseline-only` for a free run).

## Limitations
- Postgres only. SQL analysis is regex-based: CTE-wrapped DML, `DO $$` blocks, volatile defaults, enum changes and `DROP INDEX` without CONCURRENTLY are not covered.
- Invariants are only as good as the seed data, and lock judgments are only as good as the `est_rows` values in the policy file.
- The callers scan is text search within the current repo. Dynamic SQL in this repo is reported as *unresolved*. **Callers in other repos are invisible**: list known consumers in LEARNINGS.md and CODEOWNERS until a cross-repo scan exists.
- The edit guard hook covers Edit and Write only. It allows the edit when it can't decide (no base branch, unreadable config, uv missing), and shell edits bypass it. The CI gate is the enforcement; the hook is a convenience.
- The throwaway-database guard for Alembic covers engines built through SQLAlchemy (sync and async). An `env.py` that opens a raw driver connection itself (e.g. `psycopg.connect(...)`) bypasses it.
- A migration can be semantically wrong and still pass every check. Questions, the verifier and human review reduce that risk; they don't remove it.

## Rolling out to a team
What should be in place before trusting its output in production (details in [docs/rollout.md](docs/rollout.md)):
1. **CI is the gate, not the laptop.** The reusable workflow is a required check with no LLM in it. REFUSED blocks the merge. NEEDS-HUMAN needs a code owner's approval (the label plus CODEOWNERS review). GO still gets normal review and never auto-merges.
2. **Humans own the policy.** `schema-guard.yaml` and `LEARNINGS.md` are code-owned. Checks always use the base branch's policy, so a PR can't loosen its own rules. The org-wide rules live in one `extends:` file with `locked:` keys.
3. **Real sizes and real-looking data.** `est_rows` refreshed from production statistics by a scheduled job, and seed data from an anonymised production sample, not hand-written rows.
4. **Pin and stage releases.** Pin the plugin and `sg-version` to a tag, with a `next` channel for 2–3 volunteers. Any change to the skill, rules or model must pass the eval suite (accuracy and consistency) before `stable` moves.
5. **Measure it.** Track the verdict mix (above ~40% NEEDS-HUMAN, people route around it), overrides, and every incident that got a GO. Each incident becomes an eval case and a rule.
6. **Close the known gaps first:** a cross-repo callers scan (an org code-search index), and running the gate against a production-shaped snapshot for the biggest tables.

## More
- [docs/build-notes.md](docs/build-notes.md): how it was built with AI, the design decisions, and lessons learned.
- [docs/architecture.md](docs/architecture.md): the design and the reasons behind it.
- [docs/rollout.md](docs/rollout.md): the full rollout plan and risks.
- [docs/adding-a-framework.md](docs/adding-a-framework.md): how to add a migration framework.
- [CONTRIBUTING.md](CONTRIBUTING.md) · [MIT license](LICENSE)
