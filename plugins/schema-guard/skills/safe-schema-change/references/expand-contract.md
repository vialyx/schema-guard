# Expand / contract patterns

Every change is split so that **each deploy is backward compatible with the code running before it**.
Expand = additive, ships now. Contract = removes the old shape, ships later (follow-up ticket) once no code uses it.

| Goal | Expand (now) | Contract (later) |
|---|---|---|
| Add a column | `ADD COLUMN x <type> NULL` (or with a constant, non-volatile `DEFAULT`) | optional: enforce NOT NULL (see below) |
| Make a column NOT NULL on an existing table | backfill in batches; `ADD CONSTRAINT x_nn CHECK (x IS NOT NULL) NOT VALID`; then `VALIDATE CONSTRAINT x_nn` (separate migration) | `ALTER COLUMN x SET NOT NULL` (PG12+ uses the validated CHECK, no scan); drop the CHECK |
| Rename a column | add new column; dual-write in code; backfill; switch reads | drop old column after all callers moved |
| Change a column type | add new column of the new type; dual-write; backfill; switch reads | drop old column |
| Drop a column | remove all reads/writes in code (deploy) | `DROP COLUMN` in a later migration |
| Drop a table | stop all access; optionally rename to `_deprecated_<name>` | drop after a retention period (never for ledger/audit) |
| Add an index to an existing table | `CREATE INDEX CONCURRENTLY` outside a transaction | — |
| Add a foreign key | `ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID` | `VALIDATE CONSTRAINT` in a separate migration |
| Add a unique constraint | `CREATE UNIQUE INDEX CONCURRENTLY` | `ADD CONSTRAINT ... UNIQUE USING INDEX` |
| Backfill data | a separate, idempotent, batched job (e.g. 10k ids per batch, `WHERE x IS NULL`), not inside the DDL migration for hot tables | — |
| Correct ledger data | **never UPDATE/DELETE** — insert reversing/compensating entries | — |

## Lock hygiene (Postgres)
- Start migrations that ALTER existing tables with `SET lock_timeout = '3s'` (and a sane `statement_timeout`), so a blocked ALTER fails fast instead of queueing every query behind it.
- `ALTER TABLE ... ADD COLUMN` with no default or a constant default is metadata-only (PG11+). A volatile default (`now()`, `gen_random_uuid()`) rewrites the table.
- Type changes almost always rewrite the table and hold ACCESS EXCLUSIVE for the whole rewrite.

## Money and quantities
- Money: `BIGINT` minor units (`*_cents`) or `NUMERIC(p,s)`. Never `REAL`/`DOUBLE PRECISION`.
- Weights/quantities: `NUMERIC(14,3)` in the org's canonical unit (see LEARNINGS.md). Put the unit in the column name (`_kg`).
- Signed values: say what the sign means in a column comment and a CHECK constraint.
