-- Material catalog: grades Acme buys and sells.
CREATE TABLE materials (
    id            bigint PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    name          text NOT NULL,
    family        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
