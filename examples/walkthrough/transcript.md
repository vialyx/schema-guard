# Walkthrough transcript

## Turn 1 — engineer: “Procurement needs grade deductions on orders: inspectors find contamination at goods-in, and reconciliation should use the accepted weight instead of the full net weight. Can you add that?”

I'll start with detection and read the policy files.

> **Bash** `sg detect`

> **Read** `LEARNINGS.md`

> **Read** `schema-guard.yaml`

> **Bash** `sg investigate --path services/ledger-api --tables orders --columns orders.net_weight_kg`

> **Grep**

> **Read** `services/ledger-api/app/services/reconciliation.py`

> **Read** `services/legacy-sync/sync.py`

> **Read** `CODEOWNERS`

Business meaning is unresolved, so per Phase 2 I'll ask before generating anything. First I'll check the card format.

> **Bash** `cat "/Users/maksimvialykh/Library/Application Support/Claude/scratch-workspaces/2373eecf-f1a9-4218-9226-33794f158cc9/b6cf10f0-5386-4f06-889f-3756b5c72a79/scratch-2026-09-29-2bba07/schema-guard/plugins`

> **Read** `/Users/maksimvialykh/Library/Application Support/Claude/scratch-workspaces/2373eecf-f1a9-4218-9226-33794f158cc9/b6cf10f0-5386-4f06-889f-3756b5c72a79/scratch-2026-09-29-2bba07/schema-guard/plugins/schema-guard/skills/safe-schema-change/references/verdict-card.md`

> **Write** `.schema-guard/llm.json`

> **Bash** `sg verdict --path services/ledger-api --llm .schema-guard/llm.json; echo "exit=$?"`

> **Bash** `sg verdict --path services/ledger-api --llm .schema-guard/llm.json`

## schema-guard: 🟡 NEEDS-HUMAN

I haven't written a migration yet. I need answers to five questions first. The verdict card is saved at `.schema-guard/card.md` and can be pasted into the ticket.

1. **Shape of the deduction.** Is it absolute kg, a percentage of net weight, or the accepted weight recorded directly? I recommend absolute kg, as `grade_deduction_kg NUMERIC(14,3)`.
2. **One or many per order.** Do you need a single total on `orders`, or a child table with a reason and inspector for each finding? I recommend the single total unless procurement needs a per-finding audit trail.
3. **NULL and backfill.** I recommend NULL for "not inspected", treated as 0 deduction, with no backfill. That keeps the change to a nullable column add on `orders`.
4. **Orders already billed.** `ledger_entries` are append-only and were booked on full net weight. Once a deduction is recorded on a billed order, `reconcile()` will flag a mismatch. Finance would then post a reversing entry plus a corrected one. Is that acceptable, or should deductions only be allowed before billing? I recommend the first option. The migration won't touch `ledger_entries`.
5. **Legacy ERP sync.** `sync.py` builds SQL dynamically and can write to `orders` columns. @acme/integrations should confirm the ERP keeps receiving full `net_weight_kg`. @acme/finance-eng should sign off on changing the billed weight in `reconciliation.py`.

Once you answer, I'll write migration 0005, update the model and `accepted_weight_kg`, add tests, and run `sg check`.

_turns: 14, cost: $0.29, time: 40s_

## Turn 2 — engineer: “Answers: the deduction is a weight in kilograms, recorded by the inspector per order; it is never negative and can't exceed the order's net weight. Price per kg does not change - value is accepted weight x price. Historical orders have no deduction: leave them NULL, and NULL means zero. Please go ahead.”

Proceeding. First I'll look at the existing migration style, model and tests.

> **Read** `services/ledger-api/migrations/versions/0004_create_order_import_staging.py`

> **Read** `services/ledger-api/app/models.py`

> **Read** `services/ledger-api/tests/test_reconciliation.py`

Now the migrations: 0005 adds the column and the NOT VALID checks, and 0006 validates them separately, following the lock convention.

> **Write** `services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py`

> **Write** `services/ledger-api/migrations/versions/0006_validate_orders_grade_deduction.py`

> **Edit** `services/ledger-api/app/models.py`

> **Edit** `services/ledger-api/app/models.py`

> **Edit** `services/ledger-api/app/models.py`

> **Edit** `services/ledger-api/app/services/reconciliation.py`

> **Edit** `services/ledger-api/tests/test_reconciliation.py`

> **Edit** `services/ledger-api/tests/test_reconciliation.py`

> **Bash** `sg check --path services/ledger-api`

Squawk warns about a missing `statement_timeout`. I'll fix that in both migrations and re-run.

> **Edit** `services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py`

> **Edit** `services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py`

> **Edit** `services/ledger-api/migrations/versions/0006_validate_orders_grade_deduction.py`

> **Bash** `sg check --path services/ledger-api`

All checks pass. Next I'll get the optional read-only second opinion, since this changes billed-weight semantics.

> **Agent**

The verifier is running in the background. I'll write the verdict once it reports back.

> **Read** `.schema-guard/checks.json`

> **Read** `services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py`

> **Read** `services/ledger-api/migrations/versions/0006_validate_orders_grade_deduction.py`

> **Read** `services/ledger-api/app/models.py`

> **Read** `services/ledger-api/app/services/reconciliation.py`

> **Read** `services/ledger-api/tests/test_reconciliation.py`

> **Read** `services/legacy-sync/sync.py`

> **Read** `LEARNINGS.md`

```json
{"concerns": [
  {"severity": "ask",
   "what": "legacy-sync pull_updates runs UPDATE orders SET {field} = %s from ERP_FIELD_MAP plus ERP_EXTRA_FIELDS. The two new CHECK constraints are enforced on every write. ERP edits to net_weight_kg or a deduction field can now fail the whole batch (an unhandled exception, with no per-row handling before conn.commit()). Concretely, lowering net_weight_kg below an existing grade_deduction_kg violates ck_orders_grade_deduction_le_net. The field is also not in ERP_FIELD_MAP, so ERP-side deductions are never synced. Values arrive as latin-1 CSV strings, and an empty string would fail the numeric cast. The static caller check only lists these sites as unresolved and does not clear them. Per LEARNINGS 2026-07, @acme/integrations must confirm.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/legacy-sync/sync.py:59",
   "suggestion": "Get @acme/integrations sign-off. Check whether ERP_EXTRA_FIELDS in production maps anything to grade_deduction_kg. Decide whether pull_updates should handle constraint violations per row. State explicitly that the ERP does not carry deductions."},
  {"severity": "ask",
   "what": "Billing semantics change for existing data: expected value now uses net minus deduction. Historical rows are NULL, so their value is unchanged. But if a deduction is recorded after an order is already billed and posted, reconcile() reports a mismatch (the test shows +8,500). Nothing produces the reversing and corrected ledger entries, and there is no documented process or owner for that. Because the ledger is append-only, this must be done by reversal.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/ledger-api/app/services/reconciliation.py:29",
   "suggestion": "Say who or what posts the reversing and corrected AR entries when a deduction is set. Alternatively, state that the mismatch report is the intended trigger."},
  {"severity": "ask",
   "what": "Completeness: no write path exists for grade_deduction_kg. There is no API, serializer or importer change. The order importer, order_import_staging and the legacy-sync push were not updated or confirmed. Only reconciliation reads the column. Other consumers of net_weight_kg may need accepted weight: the sync push sends raw net weight to the ERP, and inventory_movements.quantity_kg is unrelated. If they should use accepted weight they are unchanged.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/ledger-api/app/models.py:39",
   "suggestion": "List the intended writers of the column. Confirm that the sync push and any other reports should keep net weight."},
  {"severity": "ask",
   "what": "Rollback is lossy: downgrade drops the column, and any deductions recorded after deploy are discarded silently. Those deductions affect billing. After a downgrade, reconciliation reverts to net weight and would flag orders whose ledger entries were already corrected. The migration docstring or comments do not state that this loss is acceptable. The roundtrip check only verifies schema, on data with no deductions.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py:38",
   "suggestion": "Document that downgrade is only safe before deductions are used. Otherwise require exporting the column first (for example into audit_log or a side table)."},
  {"severity": "note",
   "what": "The 0006 downgrade is a no-op, as documented. Downgrading from 0006 to 0005 leaves the constraints valid, which is harmless. Sequencing is sound. The 0005 expand step is backward compatible: the column is nullable, and the CHECKs pass for existing rows because the column is NULL. 0006 is a separate migration, which is fine. One caveat: the constraints are enforced from 0005 onward, so old code that writes the column would be checked. No deployed code writes it.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/ledger-api/migrations/versions/0006_validate_orders_grade_deduction.py:26",
   "suggestion": "No change needed."},
  {"severity": "note",
   "what": "Test gaps: no test exercises a deduction greater than net weight (which the DB rejects but the pure function silently turns into a negative weight and negative bill). No test covers a deduction with a billed status other than delivered, or half-up rounding with a deduction. accepted_weight_kg does not clamp or assert, so it is only safe because of the constraint. Also, Decimal(order.grade_deduction_kg or 0) treats Decimal('0') as falsy, which is harmless.",
   "where": "/private/var/folders/vs/520w5bhd1qn25ndtsmhg2zwh0000gn/T/sg-walkthrough-y3vjonoy/repo/services/ledger-api/tests/test_reconciliation.py:46",
   "suggestion": "Add a test or assertion that a deduction above net weight is rejected or flagged. Optionally add a DB-level test for the constraints."}
],
"recommend_escalation": null}
```

Writing the verdict now. The user didn't answer the legacy-sync question, and the verifier flagged it too, so it stays as the one blocking question.

> **Write** `.schema-guard/llm.json`

> **Bash** `sg verdict --path services/ledger-api --llm .schema-guard/llm.json`

I hit the usage limit, so I'm stopping here. The migration and code are written, but the verdict is 🟡 NEEDS-HUMAN because one question is still open.

**Done**
- Migrations `0005` and `0006` add the `orders.grade_deduction_kg` column (`NUMERIC(14,3)`, nullable, where NULL means 0) and its constraints.
  - `0005` adds the column and two CHECKs, that the deduction is ≥ 0 and ≤ `net_weight_kg`. The CHECKs are added `NOT VALID`.
  - `0006` validates both CHECKs.
  - Existing orders keep NULL, and both migrations set lock and statement timeouts.
- `models.py` has the new column and constraints. `accepted_weight_kg()` now subtracts the deduction, and price per kg is unchanged.
- I added tests for deductions, NULL and zero, full rejection, cancelled orders, order value, and `reconcile()` with a reversing entry.
- All `sg check` checks pass, including the round trip and invariants, and 12 tests pass. Squawk first warned about a missing `statement_timeout`, and I fixed that.
- The read-only verifier raised no escalation.

**Still open**
- **Legacy ERP sync (blocking):** @acme/integrations needs to confirm before deploy. `sync.py` `pull_updates` has no per-row error handling. An ERP edit that lowers `net_weight_kg` below an existing deduction would violate the new CHECK and fail the whole nightly batch. They should also confirm that no `ERP_EXTRA_FIELDS` entry maps to the new column and that the ERP keeps receiving full net weight.
- **Non-blocking, for the follow-up PR:**
  - Nothing writes `grade_deduction_kg` yet, so the API or importer path is still to do.
  - A deduction recorded after an order is billed shows up as a `reconcile()` mismatch, and finance-eng would have to post the reversing and corrected entries.
  - Downgrading drops the column and any deductions recorded since deploy.
  - `accepted_weight_kg()` doesn't clamp and relies on the CHECK, and there's no test for a deduction above net weight.

**Reviewers required:** @acme/eng, @acme/finance-eng and @acme/data-platform.

The verdict card is in `.schema-guard/card.md`. I didn't commit anything or touch `LEARNINGS.md`. Once integrations confirms the legacy-sync question, re-run `sg verdict` to clear the open question.

_turns: 18, cost: $0.72, time: 69s_
_turns: 4, cost: $0.72, time: 19s_
