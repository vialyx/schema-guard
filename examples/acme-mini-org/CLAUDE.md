# Acme mini-org: conventions for AI tools

B2B marketplace for industrial materials. Buyers order lots of material by weight
from suppliers; the ledger records what is owed and paid.

## Schema changes
- Every schema change goes through the `safe-schema-change` skill. Do not hand-write
  migrations without running it.
- Config lives in `schema-guard.yaml`; past lessons are in `LEARNINGS.md`. Read both first.
- Migrations: Alembic, in `services/ledger-api/migrations/versions/`. Every migration
  needs a working `downgrade()`.

## Data conventions
- Money is integer cents (`BigInteger`, column suffix `_cents`). Never float, never NUMERIC.
- Weights are kilograms as `NUMERIC(14,3)` (column suffix `_kg`). Use `Decimal` in Python.
- `ledger_entries` rows are append-only. Corrections are reversing entries, never UPDATE/DELETE.
- `audit_log` is never rewritten or dropped.

## Code
- `services/legacy-sync/sync.py` builds SQL dynamically; grep will not find all its table usage.
- Tests: `python -m pytest -q tests` from `services/ledger-api`.
