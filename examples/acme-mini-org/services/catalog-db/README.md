# catalog-db

Material catalog schema, managed as plain SQL migrations (Flyway naming):
`db/migrations/V<n>__<desc>.sql` to apply, `U<n>__<desc>.sql` to undo.
No ORM; other services read it through views. Owned by @acme/catalog.
