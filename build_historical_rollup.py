"""
Historical Rollup Builder

Reads indelco_historical.db (the frozen pre-BC270 snapshot — 597 MB
of mostly transactional rows) and writes a compact indelco_legacy.db
containing monthly aggregates plus item/location masters.

Why: loading 600 MB of row-level legacy data into sql.js alongside the
1.7 GB live DB is a memory problem in the browser. The legacy data only
exists to extend trailing analytics (multi-year demand, sales history,
cost trends) — for that, monthly buckets per Item × Location × Entry Type
are sufficient. Row-level drill on frozen data is rarely useful.

Source is opened read-only. Target is rewritten on each run.

Usage:
  python build_historical_rollup.py
  python build_historical_rollup.py --source other.db --target out.db
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def find_col(cols, *candidates):
    """Match a column by lowercased substring; lets us tolerate minor naming drift."""
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    for cand in candidates:
        for col_lower, col_real in lower.items():
            if cand in col_lower:
                return col_real
    return None


def discover(con, table):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    if not cols:
        sys.exit(f"  ABORT: table '{table}' missing from source DB")
    return cols


def count(con, table):
    return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def roll_ile(tgt, src_path):
    """Aggregate ile_transactions → legacy_monthly_demand."""
    print("\n[1/4] Rolling up ile_transactions → legacy_monthly_demand")

    # Validate columns via a read-only probe before we touch the target.
    probe = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    cols = discover(probe, 'ile_transactions')
    src_rows = count(probe, 'ile_transactions')
    probe.close()

    c_date = find_col(cols, 'posting_date')
    c_item = find_col(cols, 'item_no')
    c_loc  = find_col(cols, 'location_code')
    c_qty  = find_col(cols, 'quantity')
    c_type = find_col(cols, 'entry_type')

    missing = [n for n, v in [
        ('posting_date', c_date), ('item_no', c_item),
        ('location_code', c_loc), ('quantity', c_qty),
        ('entry_type', c_type)] if v is None]
    if missing:
        sys.exit(f"  ABORT: ile_transactions missing required cols: {missing}\n"
                 f"  available: {cols}")

    print(f"      source rows: {src_rows:,}")
    print(f"      cols: date={c_date}  item={c_item}  loc={c_loc}  qty={c_qty}  type={c_type}")

    tgt.execute("DROP TABLE IF EXISTS legacy_monthly_demand")
    tgt.execute("""
        CREATE TABLE legacy_monthly_demand (
            item_no       TEXT NOT NULL,
            location_code TEXT NOT NULL,
            year_month    TEXT NOT NULL,
            entry_type    TEXT NOT NULL,
            qty_signed    REAL NOT NULL,
            qty_abs       REAL NOT NULL,
            txn_count     INTEGER NOT NULL,
            PRIMARY KEY (item_no, location_code, year_month, entry_type)
        )
    """)

    tgt.execute(f"""
        INSERT INTO legacy_monthly_demand
            (item_no, location_code, year_month, entry_type,
             qty_signed, qty_abs, txn_count)
        SELECT
            COALESCE("{c_item}", '') AS item_no,
            COALESCE("{c_loc}",  '') AS location_code,
            substr("{c_date}", 1, 7) AS year_month,
            COALESCE("{c_type}", '') AS entry_type,
            SUM(COALESCE("{c_qty}", 0))      AS qty_signed,
            SUM(ABS(COALESCE("{c_qty}", 0))) AS qty_abs,
            COUNT(*)                         AS txn_count
        FROM src.ile_transactions
        WHERE "{c_date}" IS NOT NULL
          AND length("{c_date}") >= 7
        GROUP BY item_no, location_code, year_month, entry_type
    """)
    tgt.commit()

    out_rows = count(tgt, 'legacy_monthly_demand')
    ratio = (src_rows / out_rows) if out_rows else 0
    print(f"      target rows: {out_rows:,}  ({ratio:.1f}× compression)")


def roll_value(tgt, src_path):
    """Aggregate value_entries → legacy_value_summary."""
    print("\n[2/4] Rolling up value_entries → legacy_value_summary")

    probe = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    cols = discover(probe, 'value_entries')
    src_rows = count(probe, 'value_entries')
    probe.close()

    c_date  = find_col(cols, 'posting_date')
    c_item  = find_col(cols, 'item_no')
    c_loc   = find_col(cols, 'location_code')
    c_type  = find_col(cols, 'ile_entry_type', 'entry_type')
    c_cost  = find_col(cols, 'cost_amount_actual', 'cost_amount__actual_')
    c_sales = find_col(cols, 'sales_amount_actual', 'sales_amount__actual_')
    c_qty   = find_col(cols, 'invoiced_quantity', 'valued_quantity')

    missing = [n for n, v in [
        ('posting_date', c_date), ('item_no', c_item),
        ('location_code', c_loc),  ('entry_type', c_type),
        ('cost_amount_actual', c_cost)] if v is None]
    if missing:
        sys.exit(f"  ABORT: value_entries missing required cols: {missing}\n"
                 f"  available: {cols}")

    print(f"      source rows: {src_rows:,}")
    print(f"      cols: cost={c_cost}  sales={c_sales}  qty={c_qty}")

    tgt.execute("DROP TABLE IF EXISTS legacy_value_summary")
    tgt.execute("""
        CREATE TABLE legacy_value_summary (
            item_no            TEXT NOT NULL,
            location_code      TEXT NOT NULL,
            year_month         TEXT NOT NULL,
            entry_type         TEXT NOT NULL,
            total_cost_actual  REAL NOT NULL,
            total_sales_actual REAL NOT NULL,
            total_qty          REAL NOT NULL,
            txn_count          INTEGER NOT NULL,
            PRIMARY KEY (item_no, location_code, year_month, entry_type)
        )
    """)

    qty_expr   = f'COALESCE("{c_qty}", 0)'   if c_qty   else '0'
    sales_expr = f'COALESCE("{c_sales}", 0)' if c_sales else '0'

    tgt.execute(f"""
        INSERT INTO legacy_value_summary
            (item_no, location_code, year_month, entry_type,
             total_cost_actual, total_sales_actual, total_qty, txn_count)
        SELECT
            COALESCE("{c_item}", '') AS item_no,
            COALESCE("{c_loc}",  '') AS location_code,
            substr("{c_date}", 1, 7) AS year_month,
            COALESCE("{c_type}", '') AS entry_type,
            SUM(COALESCE("{c_cost}", 0)) AS total_cost_actual,
            SUM({sales_expr})            AS total_sales_actual,
            SUM({qty_expr})              AS total_qty,
            COUNT(*)                     AS txn_count
        FROM src.value_entries
        WHERE "{c_date}" IS NOT NULL
          AND length("{c_date}") >= 7
        GROUP BY item_no, location_code, year_month, entry_type
    """)
    tgt.commit()

    out_rows = count(tgt, 'legacy_value_summary')
    ratio = (src_rows / out_rows) if out_rows else 0
    print(f"      target rows: {out_rows:,}  ({ratio:.1f}× compression)")


def copy_table(tgt, src_path, src_table, tgt_table):
    """Copy a small reference table over verbatim, preserving schema."""
    probe = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src_rows = count(probe, src_table)
    ddl = probe.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (src_table,)
    ).fetchone()
    probe.close()
    if not ddl:
        print(f"      WARN: {src_table} not in source — skipping")
        return

    create_sql = ddl[0].replace(
        f'CREATE TABLE "{src_table}"', f'CREATE TABLE "{tgt_table}"', 1
    ).replace(
        f'CREATE TABLE {src_table}',   f'CREATE TABLE "{tgt_table}"', 1
    )
    tgt.execute(f'DROP TABLE IF EXISTS "{tgt_table}"')
    tgt.execute(create_sql)
    tgt.execute(f'INSERT INTO "{tgt_table}" SELECT * FROM src."{src_table}"')
    tgt.commit()
    print(f"      {src_table} → {tgt_table}: {count(tgt, tgt_table):,} rows "
          f"(source had {src_rows:,})")


def write_meta(tgt, src_path, started_at):
    tgt.execute("DROP TABLE IF EXISTS legacy_meta")
    tgt.execute("""
        CREATE TABLE legacy_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    src_size = Path(src_path).stat().st_size
    tgt.executemany(
        "INSERT INTO legacy_meta (key, value) VALUES (?, ?)",
        [
            ('source_path',     str(Path(src_path).resolve())),
            ('source_size_mb',  f"{src_size/1024/1024:.1f}"),
            ('rolled_up_at',    time.strftime('%Y-%m-%d %H:%M:%S')),
            ('elapsed_seconds', f"{time.time() - started_at:.1f}"),
        ]
    )
    tgt.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default='indelco_historical.db')
    ap.add_argument('--target', default='indelco_legacy.db')
    args = ap.parse_args()

    src = Path(args.source)
    tgt = Path(args.target)
    if not src.exists():
        sys.exit(f"Source not found: {src.resolve()}")
    if tgt.exists():
        print(f"Overwriting existing target: {tgt.resolve()}")
        tgt.unlink()

    started = time.time()
    print(f"Source: {src.resolve()}  ({src.stat().st_size/1024/1024:.1f} MB)")
    print(f"Target: {tgt.resolve()}")

    # uri=True so the ATTACH below can use the file:?mode=ro form, keeping the
    # source read-only even though the target connection is writable.
    tgt_con = sqlite3.connect(tgt, uri=True)
    tgt_con.execute("PRAGMA journal_mode=DELETE")  # no WAL files for the output
    src_uri = f"file:{src.resolve().as_posix()}?mode=ro"
    tgt_con.execute("ATTACH DATABASE ? AS src", (src_uri,))

    roll_ile(tgt_con, str(src))
    roll_value(tgt_con, str(src))

    print("\n[3/4] Copying small reference tables verbatim")
    copy_table(tgt_con, str(src), 'items',     'legacy_items')
    copy_table(tgt_con, str(src), 'locations', 'legacy_locations')

    print("\n[4/4] Writing meta + vacuuming")
    write_meta(tgt_con, str(src), started)
    tgt_con.execute("DETACH DATABASE src")
    tgt_con.commit()
    tgt_con.execute("VACUUM")
    tgt_con.close()

    print(f"\nDone in {time.time()-started:.1f}s")
    print(f"Output: {tgt.resolve()}  ({tgt.stat().st_size/1024/1024:.1f} MB)")


if __name__ == '__main__':
    main()
