"""
SQLite Database Inspector — schema + size + sample row per table

Standalone. Does not touch the running app. Tells you exactly what's
inside an indelco*.db file so we can decide whether legacy data is
already merged into indelco.db or sits in indelco_historical.db.

Usage:
  python db_inspect.py                          # inspects ./indelco.db
  python db_inspect.py indelco_historical.db    # inspects a specific file
  python db_inspect.py path/to/some.db
"""

import sys
import sqlite3
from pathlib import Path


def inspect(db_path):
    p = Path(db_path)
    if not p.exists():
        print(f"  NOT FOUND: {p.resolve()}")
        return

    size_mb = p.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 70}")
    print(f"  {p.resolve()}")
    print(f"  size: {size_mb:.1f} MB")
    print('=' * 70)

    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    cur = con.cursor()

    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )]
    if not tables:
        print("  (no tables)")
        con.close()
        return

    print(f"  {len(tables)} tables\n")
    print(f"  {'Table':<40s} {'Rows':>12s}   First column → sample")
    print(f"  {'-' * 40} {'-' * 12}   {'-' * 35}")

    for t in tables:
        try:
            n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except sqlite3.Error as e:
            print(f"  {t:<40s} {'ERR':>12s}   {e}")
            continue

        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        first_col = cols[0] if cols else '(no columns)'
        sample = '(empty)'
        if n > 0 and cols:
            try:
                row = cur.execute(
                    f'SELECT "{first_col}" FROM "{t}" LIMIT 1'
                ).fetchone()
                if row is not None:
                    sample = repr(row[0])
                    if len(sample) > 32:
                        sample = sample[:29] + '...'
            except sqlite3.Error:
                sample = '(read error)'

        print(f"  {t:<40s} {n:>12,d}   {first_col} → {sample}")
        if len(cols) > 1:
            extras = ', '.join(cols[1:6])
            more = f', +{len(cols)-6} more' if len(cols) > 6 else ''
            print(f"  {'':<40s} {'':>12s}   cols: {extras}{more}")

    con.close()
    print()


def main():
    targets = sys.argv[1:] or ['indelco.db']
    for db in targets:
        inspect(db)


if __name__ == '__main__':
    main()
