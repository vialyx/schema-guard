"""legacy-sync: nightly two-way sync between Acme's old ERP and the orders DB.

README: run `python sync.py --direction pull|push` from cron. Owned by
@acme/integrations. Table and column names come from ERP_FIELD_MAP and the
ERP export config at runtime, so static analysis cannot see which tables or
columns this script touches. Treat every schema change on orders,
order_import_staging or their columns as possibly affecting this script.
"""

import argparse
import os

import psycopg

# ERP field name -> orders column. Extended by ops via ERP_EXTRA_FIELDS.
ERP_FIELD_MAP = {
    "ORDNR": "order_no",
    "WGT_NET": "net_weight_kg",
    "PRC_KG": "price_per_kg_cents",
    "STAT": "status",
}

# Tables the ERP export may read from, picked per run by the ERP config.
SOURCE_TABLES = os.environ.get("ERP_SOURCE_TABLES", "orders,order_import_staging").split(",")


def field_map() -> dict[str, str]:
    extra = os.environ.get("ERP_EXTRA_FIELDS", "")
    mapping = dict(ERP_FIELD_MAP)
    for pair in filter(None, extra.split(",")):
        erp_name, column = pair.split("=", 1)
        mapping[erp_name.strip()] = column.strip()
    return mapping


def push_weights(conn: psycopg.Connection, table: str) -> list[tuple]:
    """Read order weights to send to the ERP."""
    with conn.cursor() as cur:
        if table == "order_import_staging":
            cur.execute(
                f"SELECT raw->>'ref' AS order_no, (raw->>'weight_t')::numeric * 1000 FROM {table}"
            )
        else:
            cur.execute(f"SELECT order_no, net_weight_kg FROM {table}")
        return cur.fetchall()


def pull_updates(conn: psycopg.Connection, erp_rows: list[dict]) -> int:
    """Apply ERP-side edits to orders, one field at a time."""
    mapping = field_map()
    updated = 0
    with conn.cursor() as cur:
        for row in erp_rows:
            order_no = row["ORDNR"]
            for erp_name, value in row.items():
                field = mapping.get(erp_name)
                if field is None or field == "order_no":
                    continue
                cur.execute(f"UPDATE orders SET {field} = %s WHERE order_no = %s", (value, order_no))
                updated += cur.rowcount
    conn.commit()
    return updated


def read_erp_export(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="latin-1") as fh:
        header = fh.readline().rstrip("\n").split(";")
        for line in fh:
            rows.append(dict(zip(header, line.rstrip("\n").split(";"))))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--direction", choices=["pull", "push"], required=True)
    parser.add_argument("--erp-file", default="/var/erp/export/orders.csv")
    args = parser.parse_args()

    with psycopg.connect(os.environ["LEGACY_DATABASE_URL"]) as conn:
        if args.direction == "pull":
            print(f"updated {pull_updates(conn, read_erp_export(args.erp_file))} fields")
        else:
            for table in SOURCE_TABLES:
                print(f"{table}: {len(push_weights(conn, table.strip()))} rows")


if __name__ == "__main__":
    main()
