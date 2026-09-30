# Architecture

schema-guard is two halves with a hard boundary between them.

```
            engineer / CI
                 │  "add grade deductions to orders"   or   "review migration 0005"
                 ▼
┌──────────────────────────────────────────────┐
│ Skill: safe-schema-change (SKILL.md)         │  LLM: investigates, asks, plans,
│   Phase 0 detect → 1 classify → 2 investigate│  writes migration + code, fixes
│   → (ASK?) → 3 plan → (REFUSE?) → 4 generate │  findings, explains.
│   → 5 validate → 6 verdict → 7 learn         │
└───────────────┬──────────────────────────────┘
                │ calls `sg …` (JSON in/out)           ▲ may only ESCALATE
                ▼                                      │
┌──────────────────────────────────────────────┐       │
│ Engine: `sg` (Python)                        │  Deterministic: decides the
│   detect · investigate · render · check      │  floor of the verdict.
│   verdict · guard-edit                       │
│                                              │
│   core ── registry ──┬─ adapters/  (alembic, raw_sql, …)
│                      ├─ dialects/  (postgres, …)
│                      ├─ checks/    (policy, squawk, callers, roundtrip, invariants, tests)
│                      └─ rules/     (float-on-money, destructive-change, …)
└──────────────────────────────────────────────┘
      ▲ reads                         ▲ throwaway DBs only
      │                               │
 schema-guard.yaml · CODEOWNERS ·   local initdb or $SG_DATABASE_URL
 LEARNINGS.md · git base ref · seed.sql
```

Plus two Claude Code-specific pieces in the plugin:
- **Edit guard hook** (`PreToolUse` → `sg guard-edit`): blocks Edit/Write on a migration that exists on the base ref. Enforcement does not depend on the model reading instructions.
- **`migration-verifier` subagent**: read-only, fresh context, looks for semantic issues the checks can't see. It can only recommend escalation.

## The verdict contract

| Input | Effect |
|---|---|
| any check `refuse` | REFUSED |
| any check `ask` or `skipped` | at least NEEDS-HUMAN |
| no checks ran | NEEDS-HUMAN |
| LLM `questions` non-empty | at least NEEDS-HUMAN |
| LLM `escalate` | raises to that level; **never lowers** |

`verdict.json` is validated against `sg/verdict.schema.json`. The markdown card is rendered from it, so what the engineer sees in the PR is exactly what CI saw.

## Why the split

- **Repeatability.** The same migration gets the same checks and the same verdict floor every time, whatever the prompt or the model version.
- **Trust.** "The AI said it's fine" is not evidence. "Round-trip passed, ledger balances unchanged on seed data, squawk clean" is.
- **Cost.** The expensive part (LLM) is where judgment is needed: interpreting intent, asking, writing code. Everything that can be computed is computed.
- **Portability.** `sg` runs the same in CI without an LLM (`sg check && sg verdict`). SKILL.md follows the open Agent Skills format; only the hook and subagent are Claude Code-specific.

## "Shipped" is defined by git, not by a database

A migration is *shipped* if its file exists on the base ref (`main` by default, `$SG_BASE_REF` in CI). This avoids giving the tool production credentials and makes the answer reproducible offline. Trade-off: a migration merged to main but not yet deployed is treated as shipped. That's the safe direction.

## Extension points

| To add | Write | Register in `pyproject.toml` group | Must pass |
|---|---|---|---|
| Migration framework (Django, Prisma, Rails, Flyway…) | `sg/adapters/<name>.py` implementing `MigrationAdapter` + a sample in `tests/contract/samples.py` | `schema_guard.adapters` | `tests/contract` |
| Database engine (MySQL…) | `sg/dialects/<name>.py` implementing `Dialect` | `schema_guard.dialects` | dialect tests |
| Check (e.g. gh-ost dry run, EXPLAIN on replica) | `sg/checks/<name>.py` implementing `Check` | `schema_guard.checks` | unit tests |
| Policy rule | `sg/rules/<name>.py` implementing `Rule` | `schema_guard.rules` | unit tests |
| Org knowledge (tables, money columns, invariants) | `schema-guard.yaml`, no code | — | — |

Entry points mean an organisation can ship private adapters/rules in its own package without forking this repo.
The skill text never names a framework; framework-specific guidance reaches the model via `conventions_hint()`.

## Data flow of `sg check`

1. `build_context`: find the repo root (nearest `schema-guard.yaml`), load the policy (engine defaults → `$SG_ORG_POLICY` → repo file), pick the service and adapter, list migrations, and mark each `shipped` / `new` / `shipped-EDITED` against the base ref.
2. `render_pending`: offline SQL for every new or edited migration (Alembic `--sql`, raw file contents, …).
3. Run the checks in order. A check whose requirements are missing (no DB, no squawk) returns `skipped`. A check that crashes also returns `skipped`, never `pass`.
4. Write `.schema-guard/checks.json`. Exit code = worst status (0 pass/warn, 1 ask/skipped, 2 refuse).
