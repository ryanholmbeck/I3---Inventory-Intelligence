"""
Spend Diagnostic — find where the scorecard's spend inflation comes from.

Computes per-vendor spend FOUR different ways for the same date range:

  1. OLT raw       — current scorecard logic, sums every observed_lead_times row.
                     If OLT has duplicate rows per (receipt_no, item_no),
                     spend is inflated by exactly that duplication factor.
  2. OLT dedup     — collapses OLT to one row per (receipt_no, item_no, vendor)
                     before summing. The cleanest fix if OLT row-level
                     duplication is the cause.
  3. Value Entries — sums cost_amount_actual where ile_entry_type='Purchase'.
                     This is the financial-ledger truth: every dollar that
                     posted to a vendor's GL account in this window.
  4. PO Receipts   — sums qty_received * unit_cost from purchase_orders
                     where qty_received > 0. The receiving-clerk's view of
                     what was received, before invoice posting.

The four should ideally cluster around the same number. Where they spread,
that's the disagreement we need to resolve.

Usage:
  python diag_spend.py                       # top 20 vendors, YTD
  python diag_spend.py GFI1 SPR1             # specific vendors
  python diag_spend.py --period rolling_12m  # different window
"""

import sqlite3
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent / 'indelco.db'


def get_period_range(period):
    today = datetime.now()
    if period == 'ytd':
        return today.replace(month=1, day=1).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    if period == 'rolling_12m':
        return (today - timedelta(days=365)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    if period == 'calendar_year':
        return (today.replace(month=1, day=1).strftime('%Y-%m-%d'),
                today.replace(month=12, day=31).strftime('%Y-%m-%d'))
    if period == 'last_year':
        ly = today.year - 1
        return f'{ly}-01-01', f'{ly}-12-31'
    sys.exit(f"Unknown period: {period}")


def fmt(n):
    if n is None:
        return '—'
    n = float(n)
    if abs(n) >= 1_000_000:
        return f'${n/1_000_000:>8,.2f}M'
    if abs(n) >= 1_000:
        return f'${n/1_000:>9,.1f}K'
    return f'${n:>10,.2f}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vendors', nargs='*', help='specific vendor codes (default: top 20)')
    ap.add_argument('--period', default='ytd',
                    choices=['ytd', 'rolling_12m', 'calendar_year', 'last_year'])
    ap.add_argument('--db', default=str(DB_PATH))
    args = ap.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"DB not found: {args.db}")

    start, end = get_period_range(args.period)
    print(f"DB:     {args.db}")
    print(f"Period: {args.period} ({start} → {end})\n")

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    # ── OLT duplication factor per vendor ───────────────────────────────
    dup_sql = """
        SELECT vendor_no,
               COUNT(*) AS total_rows,
               COUNT(DISTINCT receipt_no || '|' || item_no) AS unique_lines,
               ROUND(1.0 * COUNT(*) /
                     NULLIF(COUNT(DISTINCT receipt_no || '|' || item_no), 0), 2) AS dup_factor
        FROM observed_lead_times
        WHERE actual_receipt_date BETWEEN ? AND ?
          AND vendor_no IS NOT NULL AND vendor_no != ''
          AND actual_lt_days > 0
        GROUP BY vendor_no
    """
    dup_map = {}
    for vendor, n, u, df in con.execute(dup_sql, (start, end)):
        dup_map[vendor] = (n, u, df or 1.0)

    # ── 1. OLT raw (current scorecard logic)
    raw_sql = """
        SELECT vendor_no, SUM(qty_received * unit_cost)
        FROM observed_lead_times
        WHERE actual_receipt_date BETWEEN ? AND ?
          AND vendor_no IS NOT NULL AND vendor_no != ''
          AND actual_lt_days > 0
        GROUP BY vendor_no
    """
    olt_raw = dict(con.execute(raw_sql, (start, end)))

    # ── 2. OLT deduplicated (one row per receipt × item × vendor)
    dedup_sql = """
        WITH unique_lines AS (
          SELECT receipt_no, item_no, vendor_no,
                 MAX(qty_received) AS qty_received,
                 MAX(unit_cost)    AS unit_cost
          FROM observed_lead_times
          WHERE actual_receipt_date BETWEEN ? AND ?
            AND vendor_no IS NOT NULL AND vendor_no != ''
            AND actual_lt_days > 0
          GROUP BY receipt_no, item_no, vendor_no
        )
        SELECT vendor_no, SUM(qty_received * unit_cost)
        FROM unique_lines
        GROUP BY vendor_no
    """
    olt_dedup = dict(con.execute(dedup_sql, (start, end)))

    # ── 3. Value entries — financial-ledger truth
    ve_sql = """
        SELECT source_no, SUM(cost_amount_actual)
        FROM value_entries
        WHERE posting_date BETWEEN ? AND ?
          AND ile_entry_type = 'Purchase'
          AND source_no IS NOT NULL AND source_no != ''
        GROUP BY source_no
    """
    try:
        ve_spend = dict(con.execute(ve_sql, (start, end)))
    except sqlite3.OperationalError as e:
        print(f"WARN: value_entries query failed ({e}); skipping that column")
        ve_spend = {}

    # ── 4. PO-receipts — qty_received × unit_cost from purchase_orders
    po_sql = """
        SELECT vendor_no, SUM(qty_received * unit_cost)
        FROM purchase_orders
        WHERE qty_received > 0
          AND order_date BETWEEN ? AND ?
        GROUP BY vendor_no
    """
    try:
        po_spend = dict(con.execute(po_sql, (start, end)))
    except sqlite3.OperationalError:
        po_spend = {}

    # ── Pick vendors to display ─────────────────────────────────────────
    if args.vendors:
        targets = [v.upper() for v in args.vendors]
    else:
        targets = sorted(olt_raw, key=lambda v: -(olt_raw.get(v) or 0))[:20]

    # ── Print side-by-side ──────────────────────────────────────────────
    h = (f"{'Vendor':<10s} {'OLT rows':>9s} {'unique':>8s} {'×dup':>6s}  "
         f"{'OLT raw':>12s} {'OLT dedup':>12s} {'Val.Entries':>12s} {'PO recv':>12s}")
    print(h)
    print('-' * len(h))
    for v in targets:
        n, u, df = dup_map.get(v, (0, 0, None))
        print(f"{v:<10s} {n:>9,d} {u:>8,d} {(df or 1):>6.2f}  "
              f"{fmt(olt_raw.get(v)):>12s} {fmt(olt_dedup.get(v)):>12s} "
              f"{fmt(ve_spend.get(v)):>12s} {fmt(po_spend.get(v)):>12s}")
    print()
    print("Reading the table:")
    print("  ×dup        = OLT rows / unique (receipt × item).  1.00 = no dup.")
    print("  OLT raw     = current scorecard sum (after the fan-out fix).")
    print("  OLT dedup   = same, but collapsed to one row per (receipt × item).")
    print("  Val.Entries = SUM(cost_amount_actual) where ile_entry_type='Purchase'.")
    print("                Financial ledger truth — what posted to vendor's GL.")
    print("  PO recv     = SUM(qty_received × unit_cost) from purchase_orders.")
    print()
    print("If OLT dedup ≈ Val.Entries → switch scorecard to OLT dedup.")
    print("If OLT dedup still high vs Val.Entries → underlying OLT data has more")
    print("  duplication than just receipt × item (probably re-archived POs).")
    print("  In that case the cleanest fix is to use Val.Entries for spend and")
    print("  keep OLT only for the LT / OTD / fill-rate metrics it's really for.")

    con.close()


if __name__ == '__main__':
    main()
