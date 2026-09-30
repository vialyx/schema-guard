# Schema learnings

A log of lessons about Acme's database schema, newest first.

How entries get added: when the `safe-schema-change` skill finds something
worth remembering (an incident, a convention, a check that caught a problem),
it proposes an entry in its report. A human reviews it and merges it here via
a normal PR. The skill reads this file before planning any schema change, but
never edits it directly.

Format: `## YYYY-MM: short title`, then a few lines of context and the rule.

## 2026-07: order_import_staging is scratch, but legacy-sync reads it

The staging table is classed `scratch`, so dropping or reshaping it looks safe.
`services/legacy-sync/sync.py` builds table names at runtime, so grep does not
find every reader. Rule: before changing any table, also check dynamic SQL
callers and ask the owning team (@acme/integrations).

## 2026-05: ledger_entries are append-only

A backfill that ran `UPDATE ledger_entries SET amount_cents = ...` broke the
monthly close because balances no longer matched posted statements.
Rule: never UPDATE or DELETE ledger rows. Corrections are reversing entries
(same account, negated amount, memo pointing at the original). Only
`status` may move from `pending` to `posted`, and `posted_at` gets set once.

## 2026-03: NOT NULL on inventory_movements locked writes for 9 minutes

`ALTER TABLE inventory_movements ALTER COLUMN yard_code SET NOT NULL` scanned
40M rows under an ACCESS EXCLUSIVE lock and blocked all yard scanners.
Rule: on hot tables add `CHECK (col IS NOT NULL) NOT VALID`, then
`VALIDATE CONSTRAINT` in a separate transaction, then SET NOT NULL (Postgres
12+ skips the scan when a valid check exists). Set `lock_timeout` on every
migration.

## 2026-01: units for money and weight

Money is stored as `bigint` cents (`*_cents`). Weights are `NUMERIC(14,3)`
kilograms (`*_kg`). A float column once lost 0.4 kg per order in rounding.
Rule: never use float/real for money or weight; never store money as NUMERIC.
Do the math in `Decimal` and round half-up to whole cents at the edge.
