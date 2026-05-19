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
import time
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent / 'indelco.db'


def step(msg):
    """Print a progress marker, flushed immediately so we can see where it hangs."""
    print(f"  · {msg}", flush=True)


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

    # ── Probe: what does ile_entry_type look like in value_entries? ─────
    # SSMS may export the BC enum as the integer (0=Purchase, 1=Sale, etc.)
    # or as the text label depending on the driver. We have to discover it.
    step("probing value_entries.ile_entry_type distribution")
    probe = list(con.execute("""
        SELECT ile_entry_type, COUNT(*) AS n
        FROM value_entries
        GROUP BY ile_entry_type
        ORDER BY n DESC
        LIMIT 10
    """))
    print("value_entries.ile_entry_type distribution:")
    for v, n in probe:
        print(f"  {repr(v):>20s}  {n:>12,d}")
    print()

    # Pick the value that means "Purchase". BC int code is 0; text is 'Purchase'.
    candidates = ['Purchase', 'purchase', '0', 0]
    purchase_code = next((v for v, _ in probe if v in candidates), None)
    print(f"Using purchase code: {repr(purchase_code)}" if purchase_code is not None
          else "WARN: couldn't auto-detect a 'Purchase' value in ile_entry_type")
    print()

    # If specific vendors were requested, scope the heavy spend queries to
    # just those — turns 3.7M-row scans into per-vendor index seeks. Without
    # this, the value_entries / ILE→VE queries can take minutes on large DBs.
    vendor_in = ''
    vendor_params = []
    if args.vendors:
        upper = [v.upper() for v in args.vendors]
        placeholders = ','.join('?' for _ in upper)
        vendor_in = f' AND source_no IN ({placeholders})'
        vendor_params = upper

    # OLT-side vendor scope uses vendor_no (not source_no like the VE queries).
    olt_vendor_in = ''
    olt_vendor_params = []
    if args.vendors:
        upper = [v.upper() for v in args.vendors]
        ph = ','.join('?' for _ in upper)
        olt_vendor_in = f' AND vendor_no IN ({ph})'
        olt_vendor_params = upper

    # ── OLT duplication factor per vendor ───────────────────────────────
    step("computing OLT duplication factor")
    t0 = time.time()
    dup_sql = f"""
        SELECT vendor_no,
               COUNT(*) AS total_rows,
               COUNT(DISTINCT receipt_no || '|' || item_no) AS unique_lines,
               ROUND(1.0 * COUNT(*) /
                     NULLIF(COUNT(DISTINCT receipt_no || '|' || item_no), 0), 2) AS dup_factor
        FROM observed_lead_times
        WHERE actual_receipt_date BETWEEN ? AND ?
          AND vendor_no IS NOT NULL AND vendor_no != ''
          AND actual_lt_days > 0
          {olt_vendor_in}
        GROUP BY vendor_no
    """
    dup_map = {}
    for vendor, n, u, df in con.execute(dup_sql, [start, end] + olt_vendor_params):
        dup_map[vendor] = (n, u, df or 1.0)
    step(f"  done in {time.time()-t0:.1f}s")

    # ── 1. OLT raw (current scorecard logic)
    step("OLT raw spend")
    t0 = time.time()
    raw_sql = f"""
        SELECT vendor_no, SUM(qty_received * unit_cost)
        FROM observed_lead_times
        WHERE actual_receipt_date BETWEEN ? AND ?
          AND vendor_no IS NOT NULL AND vendor_no != ''
          AND actual_lt_days > 0
          {olt_vendor_in}
        GROUP BY vendor_no
    """
    olt_raw = dict(con.execute(raw_sql, [start, end] + olt_vendor_params))
    step(f"  done in {time.time()-t0:.1f}s")

    # ── 2. OLT deduplicated (one row per receipt × item × vendor)
    step("OLT deduplicated spend")
    t0 = time.time()
    dedup_sql = f"""
        WITH unique_lines AS (
          SELECT receipt_no, item_no, vendor_no,
                 MAX(qty_received) AS qty_received,
                 MAX(unit_cost)    AS unit_cost
          FROM observed_lead_times
          WHERE actual_receipt_date BETWEEN ? AND ?
            AND vendor_no IS NOT NULL AND vendor_no != ''
            AND actual_lt_days > 0
            {olt_vendor_in}
          GROUP BY receipt_no, item_no, vendor_no
        )
        SELECT vendor_no, SUM(qty_received * unit_cost)
        FROM unique_lines
        GROUP BY vendor_no
    """
    olt_dedup = dict(con.execute(dedup_sql, [start, end] + olt_vendor_params))
    step(f"  done in {time.time()-t0:.1f}s")

    # ── 3. Value entries — financial-ledger truth ─────────────────────
    step("Val.Entries spend (cost_amount_actual filtered by ile_entry_type)")
    t0 = time.time()
    ve_spend = {}
    if purchase_code is not None:
        ve_sql = f"""
            SELECT source_no, SUM(cost_amount_actual)
            FROM value_entries
            WHERE posting_date BETWEEN ? AND ?
              AND ile_entry_type = ?
              AND source_no IS NOT NULL AND source_no != ''
              {vendor_in}
            GROUP BY source_no
        """
        try:
            ve_spend = dict(con.execute(ve_sql, [start, end, purchase_code] + vendor_params))
        except sqlite3.OperationalError as e:
            print(f"WARN: value_entries query failed ({e})")
    step(f"  done in {time.time()-t0:.1f}s")

    # (Removed the ILE→VE cross-check query — without an index on
    # value_entries.ile_entry_no it's a 1.6M × 3.7M nested scan that
    # hangs the diagnostic. Val.Entries alone is sufficient; the index
    # for re-enabling this cross-check is added in build_schema().)
    po_spend = {}

    # ── Pick vendors to display ─────────────────────────────────────────
    if args.vendors:
        targets = [v.upper() for v in args.vendors]
    else:
        targets = sorted(olt_raw, key=lambda v: -(olt_raw.get(v) or 0))[:20]

    # ── Print side-by-side ──────────────────────────────────────────────
    h = (f"{'Vendor':<10s} {'OLT rows':>9s} {'unique':>8s} {'×dup':>6s}  "
         f"{'OLT raw':>12s} {'OLT dedup':>12s} {'Val.Entries':>12s}")
    print(h)
    print('-' * len(h))
    for v in targets:
        n, u, df = dup_map.get(v, (0, 0, None))
        print(f"{v:<10s} {n:>9,d} {u:>8,d} {(df or 1):>6.2f}  "
              f"{fmt(olt_raw.get(v)):>12s} {fmt(olt_dedup.get(v)):>12s} "
              f"{fmt(ve_spend.get(v)):>12s}")
    print()
    print("Reading the table:")
    print("  ×dup        = OLT rows / unique (receipt × item).  1.00 = no dup.")
    print("  OLT raw     = current scorecard sum (after the fan-out fix).")
    print("  OLT dedup   = same, but collapsed to one row per (receipt × item).")
    print("  Val.Entries = SUM(value_entries.cost_amount_actual) where")
    print(f"                ile_entry_type = {repr(purchase_code)}  (auto-detected).")
    print("                Financial-ledger truth — what posted to vendor's GL.")
    print()
    print("If Val.Entries ≈ your ground truth → that's the right source.")
    print("Likely fix: switch the scorecard's spend column to value_entries and")
    print("keep observed_lead_times for LT / OTD / fill-rate metrics only.")

    con.close()


if __name__ == '__main__':
    main()
