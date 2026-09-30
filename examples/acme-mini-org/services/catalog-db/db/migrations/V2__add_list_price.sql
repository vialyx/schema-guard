-- Reference list price per kg, in integer cents (see LEARNINGS.md: money is bigint cents).
ALTER TABLE materials ADD COLUMN list_price_per_kg_cents bigint;
