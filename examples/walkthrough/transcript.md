# Walkthrough transcript

## Turn 1 — engineer: “Procurement needs grade deductions on orders: inspectors find contamination at goods-in, and reconciliation should use the accepted weight instead of the full net weight. Can you add that?”

I'll start with detection and read the config and learnings.

> **Bash** `sg detect; echo ---; cat schema-guard.yaml; echo ---; cat LEARNINGS.md`

> **Bash** `sg investigate --tables orders --columns orders.net_weight_kg; echo ---; grep -rn "net_weight\|reconcil" --include=*.py --include=*.sql . | grep -v migrations/versions | head -40`

> **Bash** `cd /tmp/sg-walkthrough/repo/services && cat ledger-api/app/services/reconciliation.py; sed -n 1,70p legacy-sync/sync.py`

The meaning of "deduction" is unresolved, and reconciliation drives money, so I'm stopping to ask before writing any migration.

> **Bash** `cd /tmp/sg-walkthrough/repo && sg verdict --intent "Add grade/contamination deductions to orders so reconciliation bills on accepted weight instead of full net weight" \
--question "Representation: st`

> **Write** `.schema-guard/llm.json`

> **Bash** `cd /tmp/sg-walkthrough/repo && sg verdict --llm .schema-guard/llm.json; echo exit=$?`

> **Bash** `cd /tmp/sg-walkthrough/repo && sg verdict --llm .schema-guard/llm.json`

**🟡 NEEDS-HUMAN.** I haven't written a migration, changed any code, or run `sg check`. Reconciliation drives money owed, and five things need your answer first. Each has my recommended default, so replying "go with the defaults" is enough.

1. **Representation:** store a deduction (`orders.deduction_kg`) or the accepted weight itself? I recommend a nullable `deduction_kg`, which keeps `net_weight_kg` as the goods-in record.
2. **Units and sign:** is it kilograms, non-negative and never above `net_weight_kg`? Or is it a percentage, tonnes or cents?
3. **Existing rows:** NULL (no inspection recorded, treated as 0) or `0.000`? I recommend NULL with no backfill, so the change on `orders` (250k rows) is quick and safe to deploy.
4. **Posted receivables:** orders that already have a posted `1200-AR` entry at full weight will show up as reconcile mismatches. Should corrections be reversing ledger entries made in a separate PR? I recommend yes, since ledger rows are append-only.
5. **`legacy-sync`:** it pushes `net_weight_kg` to the ERP and applies ERP edits with dynamic `UPDATE`s. Does the ERP need to see the deduction or accepted weight? I recommend no ERP change now, and telling @acme/integrations.

Once you answer, I'll write migration `0005`, update the model and `accepted_weight_kg()`, add tests and run `sg check`. The card and plan are in `.schema-guard/llm.json`.

_turns: 8, cost: $0.19, time: 27s_

## Turn 2 — engineer: “Answers: the deduction is a weight in kilograms, recorded by the inspector per order; it is never negative and can't exceed the order's net weight. Price per kg does not change - value is accepted weight x price. Historical orders have no deduction: leave them NULL, and NULL means zero. Please go ahead.”

> **Bash** `cd /tmp/sg-walkthrough/repo/services/ledger-api && cat migrations/versions/0004*.py; sed -n 1,45p app/models.py; sed -n 1,45p tests/test_reconciliation.py; ls tests; grep -n "orders" -A8 seed.sql | he`

> **Bash** `cat > migrations/versions/0005_add_orders_deduction_kg.py <<'EOF'
"""add orders.deduction_kg (inspector grade/contamination deduction)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 10:00:00`

I'll use the Write and Edit tools instead.

> **Write** `services/ledger-api/migrations/versions/0005_add_orders_deduction_kg.py`

> **Read** `services/ledger-api/app/models.py`

> **Read** `services/ledger-api/app/routes/orders.py`

> **Edit** `services/ledger-api/app/models.py`

> **Edit** `services/ledger-api/app/models.py`

> **Edit** `services/ledger-api/app/services/reconciliation.py`

> **Edit** `services/ledger-api/app/routes/orders.py`

> **Edit** `services/ledger-api/app/routes/orders.py`

> **Edit** `services/ledger-api/tests/test_reconciliation.py`

> **Edit** `services/ledger-api/tests/test_reconciliation.py`

> **Bash** `cd /tmp/sg-walkthrough/repo && sg check --path services/ledger-api`

> **Edit** `services/ledger-api/migrations/versions/0005_add_orders_deduction_kg.py`

> **Edit** `services/ledger-api/migrations/versions/0005_add_orders_deduction_kg.py`

> **Bash** `cd /tmp/sg-walkthrough/repo && sg check --path services/ledger-api | grep -E '"(name|status|worst|rule)"'`

> **Write** `.schema-guard/llm.json`

Writing the verdict file now (my last call had a stray parameter).

> **Write** `.schema-guard/llm.json`

> **Bash** `sg verdict --llm .schema-guard/llm.json`

**🟡 NEEDS-HUMAN.** The migration and code are written and pass every check except one. That one needs an answer from @acme/integrations before it ships.

**What I added**
- **Migration `0005`:** a nullable `orders.deduction_kg NUMERIC(14,3)`. There is no backfill, so historical rows stay NULL, which means no deduction.
- **Constraint:** `CHECK (0 <= deduction_kg <= net_weight_kg)`, added as `NOT VALID` and then validated in a separate step. `lock_timeout` and `statement_timeout` are set.
- **Reconciliation:** `accepted_weight_kg()` now returns `net_weight_kg − COALESCE(deduction_kg, 0)`. Value is still accepted weight × price. Cancelled and rejected orders still bill zero.
- **Other code:** the `Order` model and the order endpoint (which now returns `deduction_kg` and `accepted_weight_kg`) are updated, with 5 new tests.
- **`sg check`:** policy, squawk, up→down→up round-trip, invariants and tests (11 passed) all pass.

**The open item:** `services/legacy-sync/sync.py:59` runs dynamic `UPDATE orders SET {field}=…`, including `net_weight_kg`. If an ERP pull sets `net_weight_kg` below a recorded deduction, it violates the CHECK and could abort the nightly batch. Either @acme/integrations confirms that's acceptable or adds a guard, or we drop the CHECK and enforce the rule in the application only. I kept the CHECK because you said the deduction can't exceed net weight.

**Two things you didn't explicitly answer, where I used my defaults:**
- **Already-posted receivables:** orders that already have a posted `1200-AR` entry at full weight will show as reconcile mismatches once a deduction is set. Fixing them needs reversing ledger entries in a separate change.
- **ERP:** it doesn't receive the deduction yet, and no code writes `deduction_kg` yet. The inspector write path is a follow-up.

**Reviewers:** @acme/eng, @acme/finance-eng and @acme/data-platform. Say if you'd rather drop the CHECK and I'll change the migration and re-run.

_turns: 20, cost: $0.48, time: 70s_
