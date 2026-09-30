## schema-guard: 🟡 NEEDS-HUMAN
**Intent:** Record per-order contamination deductions (kg) found at goods-in, and make reconciliation bill on accepted weight (net minus deduction) at unchanged price per kg
Adds nullable orders.grade_deduction_kg NUMERIC(14,3) (NULL = 0) with CHECKs >= 0 and <= net_weight_kg (NOT VALID in 0005, validated in 0006); accepted_weight_kg() subtracts it. All sg checks pass; one integrations sign-off is still open.

**Why not GO**
- 1 open question(s) for a human

**Questions for a human (answer, then re-run)**
1. legacy-sync: @acme/integrations must confirm before deploy. pull_updates runs UPDATE orders SET {field} = ... for ERP fields (including ERP_EXTRA_FIELDS) with no per-row error handling, so an ERP edit that lowers net_weight_kg below an existing deduction now violates ck_orders_grade_deduction_le_net and fails the whole nightly batch. Confirm no ERP_EXTRA_FIELDS maps to grade_deduction_kg, that ERP keeps receiving full net_weight_kg, and whether pull_updates should handle constraint violations per row. Recommended: get sign-off, then handle per-row failures in a follow-up.

**Tables touched**
- `orders` (core, ~250,000 rows)

**Plan**
- [expand] 0005: ADD COLUMN orders.grade_deduction_kg NUMERIC(14,3) NULL + two CHECKs NOT VALID (lock_timeout/statement_timeout set)
- [expand] 0006: VALIDATE both CHECKs
- [expand] models.py: column and constraints; reconciliation.accepted_weight_kg subtracts COALESCE(deduction, 0)
- [expand] tests: deduction, NULL/zero, full rejection, cancelled, value, reconcile with reversal

| Check | Status | Summary |
|---|---|---|
| policy | ✅ pass | no findings across 2 migrations |
| callers | ✅ pass | 4 caller reference(s) across 1 target(s) |
| squawk | ✅ pass | clean |
| roundtrip | ✅ pass | up/down/up clean; schema restored exactly |
| invariants | ✅ pass | 3 invariant(s) hold before/after and on re-run |
| tests | ✅ pass | 12 passed in 0.01s |

**Suggestions (non-blocking)**
- Billed orders: recording a deduction after posting makes reconcile() report a mismatch. Name who posts the reversing and corrected 1200-AR entries (finance-eng), or state that the mismatch report is the trigger.
- No write path for grade_deduction_kg exists yet (API/importer); list the intended writers in the follow-up PR.
- Downgrade drops the column and silently discards deductions recorded after deploy; only safe before the column is in use.
- accepted_weight_kg does not clamp; it relies on the DB CHECK. Add a test or guard for deduction > net weight.

**Required reviewers:** @acme/eng @acme/finance-eng @acme/data-platform

<details><summary>Files</summary>

- `services/ledger-api/app/models.py`
- `services/ledger-api/app/services/reconciliation.py`
- `services/ledger-api/migrations/versions/0005_add_orders_grade_deduction.py`
- `services/ledger-api/migrations/versions/0006_validate_orders_grade_deduction.py`
- `services/ledger-api/tests/test_reconciliation.py`
</details>
