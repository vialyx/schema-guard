## schema-guard: 🟡 NEEDS-HUMAN
**Intent:** Add grade/contamination deductions to orders so reconciliation bills on accepted weight instead of full net weight
No migration written yet. Reconciliation drives money owed, and the meaning of the deduction, existing-row semantics and effect on posted ledger entries are unresolved.

**Why not GO**
- no checks were run
- 5 open question(s) for a human

**Questions for a human (answer, then re-run)**
1. Representation: store the deduction (orders.deduction_kg NUMERIC(14,3); accepted = net_weight_kg - deduction_kg) or the accepted weight itself? Recommended: deduction_kg, nullable, so net_weight_kg stays the immutable goods-in record.
2. Units/sign: confirm the deduction is kilograms, non-negative, NUMERIC(14,3) (not a percentage, tonnes, or cents). Recommended: kg, non-negative, never above net_weight_kg.
3. Existing rows: NULL (no inspection recorded, treated as 0) or explicit 0.000? Recommended: NULL, no backfill.
4. Posted receivables: orders with a deduction and a posted 1200-AR entry at the old full-weight value will show as reconcile() mismatches. Should corrections be reversing ledger entries created by a separate process/PR (no UPDATE/DELETE)? Recommended: yes, out of scope here.
5. legacy-sync (@acme/integrations) pushes net_weight_kg to the ERP and applies ERP edits via dynamic UPDATEs. Should the ERP receive accepted weight or the deduction, and may ERP pulls write the new column? Recommended: no ERP change now; notify @acme/integrations and keep the column out of ERP_FIELD_MAP.

**Plan**
- [expand] Add nullable orders.deduction_kg NUMERIC(14,3) (metadata-only, no backfill); CHECK 0 <= deduction_kg <= net_weight_kg added NOT VALID, validated separately; update the model; accepted_weight_kg() = net_weight_kg - COALESCE(deduction_kg, 0); tests
- [contract — follow-up ticket] Optionally VALIDATE the check and decide whether deduction_kg becomes NOT NULL DEFAULT 0 once inspection data is complete

**Checks:** none run yet (run `sg check` once a migration exists).
