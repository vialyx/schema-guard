## schema-guard: 🟡 NEEDS-HUMAN
**Intent:** Add inspector grade/contamination deduction (kg) to orders; reconciliation bills on accepted weight = net_weight_kg - deduction_kg (NULL = 0) at unchanged price
Adds nullable orders.deduction_kg NUMERIC(14,3) with a range CHECK (0 <= deduction <= net weight), updates the model, accepted_weight_kg(), the order endpoint and tests. All checks pass except one open caller finding: legacy-sync's dynamic UPDATEs are subject to the new CHECK.

**Why not GO**
- check `callers` = ask: 8 caller reference(s) across 2 target(s)
- 1 open question(s) for a human

**Questions for a human (answer, then re-run)**
1. @acme/integrations: services/legacy-sync/sync.py:59 runs UPDATE orders SET {field}=... from ERP data, including net_weight_kg. Once an order has a deduction, an ERP pull that sets net_weight_kg below deduction_kg violates the new CHECK and can abort the nightly batch. Can they confirm this is acceptable or guard it (e.g. skip/report the row)? Alternative: drop the CHECK and enforce the rule in the application only.

**Tables touched**
- `orders` (core, ~250,000 rows)

**Plan**
- [expand] 0005: add nullable orders.deduction_kg, no backfill (historical rows NULL = no deduction); CHECK ck_orders_deduction_kg_range added NOT VALID then validated; model, reconciliation and API updated
- [contract — follow-up ticket] None required: NULL is a permanent, meaningful value. Separate follow-up: reversing 1200-AR ledger entries for already-posted orders that later get a deduction
- [contract — follow-up ticket] Follow-up: an inspector write path for deduction_kg (none exists yet)

**Checks:** ✅ policy · ❓ callers · ✅ squawk · ✅ roundtrip · ✅ invariants · ✅ tests

**Findings**
- ❓ `constraint-dynamic-writers` 3 place(s) write `orders` with SQL built at runtime; the new constraint applies to their writes and can make them fail — have the owners confirm these writes can't violate it (or guard them first); a failed write in a batch job can stop the whole batch (`services/legacy-sync/sync.py:59: cur.execute(f"UPDATE orders SET {field} = %s WHERE order_no = %s", (value, order_no))`)

**Suggestions (non-blocking)**
- Posted receivables for orders that later receive a deduction will show as reconcile() mismatches; correct them with reversing ledger entries in a separate change, never UPDATE.
- Notify @acme/integrations that the ERP does not yet receive deduction_kg or accepted weight; keep it out of ERP_FIELD_MAP for now.
- No code path writes deduction_kg yet; the inspector write endpoint is a follow-up.

**Required reviewers:** @acme/eng @acme/finance-eng @acme/data-platform

<details><summary>Files</summary>

- `services/ledger-api/app/models.py`
- `services/ledger-api/app/routes/orders.py`
- `services/ledger-api/app/services/reconciliation.py`
- `services/ledger-api/migrations/versions/0005_add_orders_deduction_kg.py`
- `services/ledger-api/tests/test_reconciliation.py`
</details>
