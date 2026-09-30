## schema-guard: 🟡 NEEDS-HUMAN
**Intent:** Record contamination (grade) deductions found at goods-in on orders, and make reconciliation bill on accepted weight instead of full net weight
Proposed: nullable orders.grade_deduction_kg NUMERIC(14,3) with a non-negative CHECK (NOT VALID, then VALIDATE), and accepted_weight_kg() subtracting COALESCE(grade_deduction_kg, 0). No migration written yet: units/semantics, existing-ledger impact and legacy-sync exposure need answers first.

**Why not GO**
- no checks were run
- 5 open question(s) for a human

**Questions for a human (answer, then re-run)**
1. Unit and shape of the deduction. Options: (a) absolute kilograms, NUMERIC(14,3) `grade_deduction_kg`; (b) a percentage of net weight (would need a different type/name, and a rounding rule); (c) inspectors record the accepted weight directly. Recommended: (a), the org unit is kg and it avoids extra rounding.
2. Cardinality. Options: (a) one deduction total per order (a single nullable column on orders); (b) several inspections/deductions per order with reason and inspector (new child table `order_grade_deductions`, reconciliation sums them). Recommended: (a) if procurement only needs a total; (b) if they need reasons/audit of each finding.
3. Meaning of NULL and backfill for existing orders. Options: (a) NULL = not yet inspected, treated as 0 deduction, no backfill; (b) NOT NULL DEFAULT 0 (constant default is safe on orders, but loses 'not inspected'). Recommended: (a), no backfill.
4. Orders already billed. Ledger rows in 1200-AR were booked on full net weight; ledger_entries are append-only. Once a deduction is recorded on such an order, reconcile() will flag a mismatch. Options: (a) accept that, and finance posts a reversing entry (same account, negated amount, memo pointing at the original) plus a corrected entry for each affected order; (b) only allow deductions before the order is billed. Recommended: (a). This migration will never touch ledger_entries.
5. Legacy ERP sync. services/legacy-sync/sync.py builds SQL dynamically (ERP_EXTRA_FIELDS can map ERP fields onto orders columns, and it pushes net_weight_kg to the ERP). Confirm with @acme/integrations that the ERP should keep receiving full net_weight_kg (not accepted weight) and that nothing there needs to read or write the new column. Also confirm @acme/finance-eng agrees with changing billed weight semantics in reconciliation.py.

**Plan**
- [expand] 0005: ADD COLUMN orders.grade_deduction_kg NUMERIC(14,3) NULL (orders is core, ~250k rows; nullable add is metadata-only)
- [expand] 0005: ADD CONSTRAINT ck_orders_grade_deduction_nonneg CHECK (grade_deduction_kg IS NULL OR grade_deduction_kg >= 0) NOT VALID; VALIDATE in a separate step
- [expand] models.py: add grade_deduction_kg; reconciliation.accepted_weight_kg subtracts COALESCE(grade_deduction_kg, 0), floored at 0
- [expand] tests for accepted_weight_kg / order_value_cents / reconcile with deductions

**Checks:** none run yet (run `sg check` once a migration exists).

**Suggestions (non-blocking)**
- Guard that grade_deduction_kg <= net_weight_kg (cross-column CHECK is fine on orders; add NOT VALID then VALIDATE)
- Expose grade_deduction_kg and accepted_weight_kg in the orders route response once the column exists
