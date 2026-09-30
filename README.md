# schema-guard

An AI engineering skill for **safe database schema changes on business-critical tables**: ledgers, money and inventory.
It investigates, asks when business meaning is unclear, writes the migration the org's way, proves it with deterministic checks, and ends with a verdict: **GO / NEEDS-HUMAN / REFUSED**.

## The problem
AI coding tools write plausible migrations, and schema changes are where "plausible" hurts most. Typical mistakes:
- FLOAT for money
- a NOT NULL that locks a 40M-row table
- a rename that breaks a dynamic-SQL job nobody knew about
- editing a migration that already shipped
- "fixing" a posted ledger row in place

These rarely fail in review, because they look fine. They fail in production.

## How it works
Two halves with a hard boundary ([docs/architecture.md](docs/architecture.md)):

- **The skill** ([SKILL.md](plugins/schema-guard/skills/safe-schema-change/SKILL.md)) is the reasoning half. It runs phased workflow: detect → classify → investigate → *ask?* → plan (expand/contract) → *refuse?* → generate → validate → verdict → learn.
- **`sg`** (the Python engine) is the deterministic half:
  - policy rules (`float-on-money`, `destructive-change`, `mutate-ledger-rows`, …);
  - [squawk](https://squawkhq.com) lock linting;
  - a repo-wide callers scan that flags dynamic SQL as *unresolved*;
  - an up→down→up round trip on a throwaway Postgres;
  - business invariants on seed data (for example, ledger balances unchanged);
  - the service's own tests.
- **Verdict rule:** checks set the floor. The LLM can only *escalate*, never lower. A skipped check or an open question never yields GO.
- **Claude Code extras:** a `PreToolUse` hook that blocks edits to shipped migrations, and a read-only `migration-verifier` subagent.

## Quick start
Requirements: [uv](https://docs.astral.sh/uv/), git, and PostgreSQL binaries (`brew install postgresql@16`) or `SG_DATABASE_URL` pointing at a disposable server. Also Node (for `npx squawk-cli`).

```bash
# try it on the demo org
bash examples/build_fixture.sh /tmp/acme && cd /tmp/acme
claude --plugin-dir /path/to/schema-guard/plugins/schema-guard
> /schema-guard:safe-schema-change add an optional inspection_notes column to orders
```

For a team, install it from the marketplace (`/plugin marketplace add <this repo>`) and enable it per repo in `.claude/settings.json`. The CI gate needs no LLM:

```bash
plugins/schema-guard/bin/sg check && plugins/schema-guard/bin/sg verdict
```

## How an engineer uses it
- **Author:** "add X to Y". The skill asks if units, NULL meaning or backfill are unclear, then writes the migration, model, code and tests, and runs the checks.
- **Review:** "review migration 0005". It gives a verdict card to paste into the PR, with required reviewers from CODEOWNERS.
- **CI:** `sg check && sg verdict` on PRs touching migrations. The exit code gates the merge.

## Context it needs (all versioned in the repo)
| File | Purpose |
|---|---|
| `schema-guard.yaml` | Services and adapters; table classes (ledger/audit/hot/core/scratch) and sizes; money/quantity column patterns; invariants; rule overrides |
| `CODEOWNERS` | Required reviewers |
| `LEARNINGS.md` | Human-curated org decisions and incident lessons. The skill proposes entries and never commits them |
| `seed.sql` | Realistic data for the invariant checks |
| git base ref | Defines what has "shipped". No production credentials are ever needed |

## Worked example
[examples/walkthrough](examples/walkthrough/) is a real two-turn session. [record.py](examples/walkthrough/record.py) reproduces it.

1. **Turn 1.** The request: "Procurement needs grade deductions on orders… reconciliation should use accepted weight."
   - The skill investigated and wrote **no migration**.
   - It returned NEEDS-HUMAN with 5 questions: units, one-vs-many deductions, NULL meaning, what to do about already-billed ledger rows (append-only → reversing entries), and the legacy ERP sync's dynamic SQL. See [card-turn1.md](examples/walkthrough/card-turn1.md).
2. **Turn 2.** After the answers, it wrote:
   - `0005`: a nullable `grade_deduction_kg NUMERIC(14,3)` plus CHECKs `>= 0` and `<= net_weight_kg`, both NOT VALID;
   - `0006`: VALIDATE;
   - updated model, reconciliation and tests (12 pass).

   All six checks were green. The verdict stayed NEEDS-HUMAN for a reason no check could see: the legacy ERP job's `UPDATE orders SET {field}` could now hit the new CHECK and fail the nightly batch, so @acme/integrations must sign off. See [card-turn2.md](examples/walkthrough/card-turn2.md) and [changes.diff](examples/walkthrough/changes.diff).

## Evals
15 labelled cases ([evals/cases](evals/cases/)):
- GO, including a false-positive trap;
- NEEDS-HUMAN: ambiguous intent, a hot-table NOT NULL, dynamic callers, a LEARNINGS-driven case, no DB available;
- REFUSED: editing a shipped migration, dropping a ledger column, FLOAT money, UPDATE of a posted entry, prompt injection in a migration docstring;
- two cases on the raw-SQL adapter.

Each run starts from a fresh fixture and goes through headless `claude -p`. The runner also scores a deterministic-only baseline.

```bash
uv run --project plugins/schema-guard/engine python evals/run_evals.py --runs 3 --model sonnet
```

Results (Sonnet, 3 runs per case):

| | correct |
|---|---|
| Deterministic baseline (no LLM) | 8/15 cases (it can't author, ask or refuse intent) |
| Skill v1 | 43/45 runs. All misses were the false-positive trap: it over-asked on a safe new table |
| Skill v2 (added a non-blocking `suggestions` channel and an explicit "blocking question" test) | trap 3/3, ambiguous case still asks 3/3 |

Cost: about $0.25 per run, 30–80 s.

What didn't work at first: the model treated nice-to-haves as blocking questions. The fix was structural (a separate `suggestions` field that can't change the verdict) plus a principle in the skill, not a case-specific patch.

## Key design decisions
- **Deterministic verdict floor.** Trust comes from evidence, not from the model's confidence.
- **"Shipped" = present on the base git ref.** Offline, reproducible, and it needs no production access.
- **Pluggable adapters, dialects, checks and rules via entry points.** Adding a framework is one file plus a contract-test sample ([docs/adding-a-framework.md](docs/adding-a-framework.md)). Alembic and raw SQL (Flyway-style) ship today, and the skill text never names a framework.
- **Org knowledge lives in YAML and LEARNINGS, not in the prompt**, so it evolves through normal PRs.
- **Expand/contract by default.** Only backward-compatible steps are generated; contract steps become follow-up tickets.
- **No vector DB, no MCP server.** The context needed is small and structured.

## Limitations and failure modes
- SQL analysis is regex-based and incomplete: CTE-wrapped DML, `DO $$` blocks, volatile defaults, enum changes and `DROP INDEX` without CONCURRENTLY are not covered.
- Postgres only today. Alembic merge revisions render against their first parent only.
- Invariants are only as good as the seed data. Table sizes come from the policy file, so stale sizes mean wrong lock judgments.
- The callers scan is text search. Callers in other repos or via dynamic SQL show up as *unresolved*, never as "none".
- The hook guards Edit/Write only. Shell edits bypass it, which is one reason the CI gate exists.
- The LLM can still write a semantically wrong but check-clean migration. The ask-first policy, the verifier subagent and human review reduce this; they don't eliminate it.

## Rolling out to a team
See [docs/rollout.md](docs/rollout.md):
- a pinned plugin with stable/next channels;
- a deterministic CI gate with branch protection (GO never auto-merges);
- policy and LEARNINGS owned by humans;
- anonymised production-sampled seed data;
- evals as the regression gate for any skill or model change;
- a pinned model with a budget;
- metrics on verdict mix, overrides and escapes.

## Layout
```
plugins/schema-guard/   skill, hook, verifier agent, bin/sg, engine/ (Python, tests)
examples/               acme-mini-org fixture, build_fixture.sh, walkthrough/
evals/                  cases/, run_evals.py, results/
docs/                   architecture, adding-a-framework, rollout
```
