"""
BC OData4 Pull — produces CSVs in SSMS_Exports format

Replaces the SSMS_Connector for sites that have BC OData4 published.
Writes CSVs to BC_Exports/ with the same column headers the SSMS
connector produces, so build_db.py runs unmodified — just point its
EXPORTS_DIR at BC_Exports instead of SSMS_Exports.

Auth: Windows Integrated (NTLM/Negotiate). Must run on a domain-joined
Windows machine on the Indelco LAN.

Setup (one-time):
  pip install requests requests-negotiate-sspi

Usage:
  python bc_pull.py                  # pull every available endpoint
  python bc_pull.py locations items  # pull only specific endpoints
  python bc_pull.py --list           # list endpoints + status (live/blocked)
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
    from requests_negotiate_sspi import HttpNegotiateAuth
except ImportError as e:
    print(f"Missing dependency: {e.name}")
    print("Install with:  pip install requests requests-negotiate-sspi")
    sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
EXPORTS_DIR = BASE_DIR / 'BC_Exports'
BC_BASE     = "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')"
COMPANY_SHORT = 'INDELCO'   # matches SSMS_Connector COMPANIES['Indelco Plastics']
ILE_YEARS_BACK = 4
HTTP_TIMEOUT = 600          # BC's mid-query page assembly can take minutes on big tables
MAX_RETRIES  = 3            # transient ReadTimeout / ConnectionError — backoff between tries


def today_stamp():
    return datetime.now().strftime('%Y_%m_%d')


def session():
    """One Session per run — keeps the NTLM auth context warm across pages."""
    s = requests.Session()
    s.auth = HttpNegotiateAuth()
    return s


# ── OData paging ──────────────────────────────────────────────────────
class BCRequestError(Exception):
    """Permanent BC error (4xx/5xx). Raised so write_csv can save the partial."""


def _get_with_retry(s, url, params=None):
    """GET with retry on transient ReadTimeout / ConnectionError.

    HTTP 4xx/5xx are NOT retried (permanent: auth, malformed query, server
    cancellation). They raise BCRequestError, which write_csv catches and
    converts into a partial-save instead of losing all in-flight rows.
    """
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = s.get(url, params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return r
            raise BCRequestError(
                f"HTTP {r.status_code} on {url}\n  body: {r.text[:400]}"
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                backoff = 2 ** attempt
                print(f"      {type(e).__name__} — retry {attempt+1}/{MAX_RETRIES} in {backoff}s")
                time.sleep(backoff)
    raise last_err


def fetch_all(s, path, params=None):
    """Walk every page of a BC OData4 collection via @odata.nextLink.

    Works for tables where BC's native paging stays fast (Locations, ItemList,
    CustomerCard, SKU with orderby). For ILE-sized tables that degrade page
    by page, use fetch_by_key_range instead.
    """
    url = f"{BC_BASE}/{path}"
    first = True
    page = 0
    total = 0
    t0 = time.time()
    while url:
        page += 1
        r = _get_with_retry(s, url, params=params if first else None)
        body = r.json()
        rows = body.get('value', [])
        total += len(rows)
        for rec in rows:
            yield rec
        url = body.get('@odata.nextLink')
        first = False
        if page == 1 or url is None or page % 5 == 0:
            elapsed = time.time() - t0
            rate = total / elapsed if elapsed else 0
            print(f"      page {page}: {total:,} rows  ({rate:,.0f}/s)")


def fetch_by_key_range(s, path, key_field, base_filter, select_fields,
                       batch_size=20000, start_from=0):
    """Manual paging by a monotonically-increasing key (primary key works best).

    BC's native @odata.nextLink slows down as $skiptoken digs deeper into the
    result set — large tables (ILE) hit BC's 8-minute query timeout before
    finishing. This approach asks for the next batch by `{key_field} gt X`
    instead, which BC can serve from its primary-key index in O(log n)
    regardless of how deep we are.

    Args:
        path: OData collection name (e.g. 'ILE')
        key_field: BC field name to range-page by (must be monotonic — Entry_No)
        base_filter: extra $filter clause (e.g. date range). Combined with AND.
        select_fields: list of BC field names for $select
        batch_size: $top per request
        start_from: initial value of {key_field}; we ask for > this
    """
    last_key = start_from
    page = 0
    total = 0
    t0 = time.time()
    while True:
        page += 1
        filter_clause = f"{key_field} gt {last_key}"
        if base_filter:
            filter_clause = f"{base_filter} and {filter_clause}"
        params = {
            '$filter':  filter_clause,
            '$select':  ','.join(select_fields),
            '$orderby': key_field,
            '$top':     str(batch_size),
        }
        r = _get_with_retry(s, f"{BC_BASE}/{path}", params=params)
        rows = r.json().get('value', [])
        if not rows:
            break
        total += len(rows)
        for rec in rows:
            yield rec
        last_key = rows[-1].get(key_field)
        if last_key is None:
            raise BCRequestError(f"key_field '{key_field}' missing from response")
        if page == 1 or len(rows) < batch_size or page % 5 == 0:
            elapsed = time.time() - t0
            rate = total / elapsed if elapsed else 0
            print(f"      batch {page}: {total:,} rows  ({rate:,.0f}/s, last {key_field}={last_key})")
        if len(rows) < batch_size:
            break


def clean(v):
    """Trim trailing spaces BC pads onto string fields; pass non-strings through."""
    if isinstance(v, str):
        return v.strip()
    return v


# ── CSV writing ───────────────────────────────────────────────────────
def write_csv(rows, headers, filename):
    """Stream rows to a temp CSV; on success rename to final, on failure
    keep the partial under a `.csv.partial` suffix so progress isn't lost.
    build_db.py globs for *.csv so the `.partial` extension keeps half-built
    files from being picked up accidentally."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    final = EXPORTS_DIR / filename
    tmp   = final.with_suffix(final.suffix + '.tmp')
    n = 0
    error = None
    with tmp.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        w.writeheader()
        try:
            for r in rows:
                w.writerow({h: clean(r.get(h, '')) for h in headers})
                n += 1
        except (requests.Timeout, requests.ConnectionError,
                requests.RequestException, BCRequestError) as e:
            error = e

    if error is None:
        tmp.replace(final)
        print(f"      wrote {n:,} rows → {final.name}")
        return n

    partial = final.parent / (final.name + '.partial')
    partial.unlink(missing_ok=True)
    tmp.replace(partial)
    print(f"      PARTIAL: {n:,} rows → {partial.name}  ({type(error).__name__})")
    raise error


def map_row(rec, mapping):
    """Translate a BC OData record into a CSV row using {csv_header: bc_field}."""
    return {csv_h: rec.get(bc_f) for csv_h, bc_f in mapping.items()}


# ── Endpoint pulls ────────────────────────────────────────────────────
def pull_locations(s):
    """Locations → CSV columns matching pull_locations() in SSMS_Connector."""
    print("  Locations...")
    mapping = {
        'Code':     'Code',
        'Name':     'Name',
        'Name 2':   'Address_2',       # SSMS uses [Name 2]; BC's closest is Address_2
        'City':     'City',
        'County':   'County',
    }
    rows = (map_row(r, mapping) for r in fetch_all(s, 'Locations'))
    return write_csv(rows, list(mapping), f'Locations_{COMPANY_SHORT}_{today_stamp()}.csv')


def pull_items(s):
    """ItemList → CSV columns matching pull_items() in SSMS_Connector.

    Some SSMS columns (Reorder Point, Safety Stock Quantity, Min/Max Order
    Quantity, Reorder Quantity, Maximum Inventory) don't exist at item level
    in BC — they live on SKU per-location. We emit them as empty strings;
    build_db.py tolerates missing values. Sales Blocked / Purchasing Blocked /
    Purchasing Code are also not on the ItemList page; left empty.
    """
    print("  Items...")
    mapping = {
        'Item No.':                'No',
        'Description':             'Description',
        'Description 2':           'ARC_Description_2',   # closest match in this BC build
        'Base Unit of Measure':    'Base_Unit_of_Measure',
        'Unit Cost':               'Unit_Cost',
        'Standard Cost':           'Standard_Cost',
        'Unit Price':              'Unit_Price',
        'Vendor No.':              'Vendor_No',
        'Blocked':                 'Blocked',
        'Lead Time Calculation':   'Lead_Time_Calculation',
        'Item Category Code':      'Item_Category_Code',
        'Last Date Modified':      'Last_Date_Modified',
        'Inventory Posting Group': 'Inventory_Posting_Group',
        'Costing Method':          'Costing_Method',
        # Placeholders — not on ItemList page; build_db.py treats absent as null/0
        'Reordering Policy':       None,
        'Reorder Point':           None,
        'Safety Stock Quantity':   None,
        'Minimum Order Quantity':  None,
        'Maximum Order Quantity':  None,
        'Reorder Quantity':        None,
        'Maximum Inventory':       None,
        'Purchasing Code':         None,
        'Sales Blocked':           None,
        'Purchasing Blocked':      None,
    }
    # Only request the BC fields we actually map — saves a lot of bandwidth
    # since ItemList returns 70 fields per row by default.
    bc_fields = [v for v in mapping.values() if v]
    params = {
        '$filter': "Blocked eq false and Type eq 'Inventory'",
        '$select': ','.join(bc_fields),
        '$orderby': 'No',
    }
    def gen():
        for r in fetch_all(s, 'ItemList', params=params):
            row = {csv_h: (r.get(bc_f) if bc_f else '') for csv_h, bc_f in mapping.items()}
            yield row
    return write_csv(gen(), list(mapping),
                     f'Items_{COMPANY_SHORT}_{today_stamp()}.csv')


def pull_qoh_from_sku(s):
    """SKU.Inventory → QoH CSV. SKU already has Inventory per Item × Location,
    so we don't need to GROUP BY ILE the way the SSMS pull does.

    Server-side filtering and projection — SKU has 38 fields, we transfer 3.
    $orderby on the composite key keeps BC from re-scanning the table on
    each page (which is what was causing the 120s timeouts).
    """
    print("  QoH (from SKU)...")
    mapping = {
        'Item No.':      'Item_No',
        'Location Code': 'Location_Code',
        'Qty on Hand':   'Inventory',
    }
    params = {
        '$filter':  "Variant_Code eq ''",
        '$select':  'Item_No,Location_Code,Inventory',
        '$orderby': 'Item_No,Location_Code',
    }
    rows = (map_row(r, mapping) for r in fetch_all(s, 'SKU', params=params))
    return write_csv(rows, list(mapping),
                     f'QoH_{COMPANY_SHORT}_{today_stamp()}.csv')


def pull_customers(s):
    """CustomerCard → Customers CSV matching pull_customers()."""
    print("  Customers...")
    mapping = {
        'No.':              'No',
        'Name':             'Name',
        'City':             'City',
        'County':           'County',
        'Country/Region Code': 'Country_Region_Code',
        'Payment Terms Code':  'Payment_Terms_Code',
        'Salesperson Code':    'Salesperson_Code',
        'Customer Posting Group': 'Customer_Posting_Group',
    }
    rows = (map_row(r, mapping) for r in fetch_all(s, 'CustomerCard'))
    return write_csv(rows, list(mapping),
                     f'Customers_{COMPANY_SHORT}_{today_stamp()}.csv')


def pull_ile(s, years_back=ILE_YEARS_BACK):
    """ILE filtered to last N years."""
    print(f"  ILE (last {years_back} years)...")
    cutoff = (datetime.now() - timedelta(days=365 * years_back)).strftime('%Y-%m-%d')
    mapping = {
        'Posting Date':       'Posting_Date',
        'Entry Type':         'Entry_Type',
        'Document Type':      'Document_Type',
        'Document No.':       'Document_No',
        'Item No.':           'Item_No',
        'Description':        'Description',
        'Location Code':      'Location_Code',
        'Quantity':           'Quantity',
        'Invoiced Quantity':  'Invoiced_Quantity',
        'Remaining Quantity': 'Remaining_Quantity',
        'Source No.':         'Source_No',
        'Source Type':        'Source_Type',
        'Entry No.':          'Entry_No',
        'Drop Shipment':      'Drop_Shipment',
        'Document Line No_':  'Document_Line_No',
        'Order Type':         'Order_Type',
        'Order No_':          'Order_No',
        'Variant Code':       'Variant_Code',
        'Branch Code':        'Global_Dimension_1_Code',
        'Global Dimension 2 Code': 'Global_Dimension_2_Code',
        'Company Source':     None,   # we tag this ourselves below
    }
    # ILE is millions of rows over a 4-year window. BC's native nextLink
    # paging degrades quadratically — page 1 runs at 500 rows/s, page 15
    # at 60 rows/s, then HTTP 408 at BC's 8-minute query timeout. Manual
    # key-range paging by Entry_No (the primary key) stays O(log n) per
    # batch regardless of how deep we are.
    bc_fields = [v for v in mapping.values() if v]

    def gen():
        for r in fetch_by_key_range(
                s, 'ILE',
                key_field='Entry_No',
                base_filter=f"Posting_Date ge {cutoff}",
                select_fields=bc_fields):
            row = {csv_h: (r.get(bc_f) if bc_f else '') for csv_h, bc_f in mapping.items()}
            row['Company Source'] = COMPANY_SHORT
            yield row

    return write_csv(gen(), list(mapping),
                     f'ILE_{COMPANY_SHORT}_{today_stamp()}.csv')


# ── Stubs for endpoints not yet published ─────────────────────────────
def _blocked(name, what):
    def stub(s):
        print(f"  [BLOCKED] {name}: waiting on IT publish of {what}")
        return 0
    return stub


pull_vendors        = _blocked('Vendors',         'Vendor List / Vendor Card (Table 23)')
pull_item_vendor    = _blocked('Item Vendor',     'Item Vendor Catalog (Table 99)')
pull_sales_lines    = _blocked('Sales Lines',     'Posted Sales Shipment Header + Line (Tables 110, 111)')
pull_value_entries  = _blocked('Value Entries',   'Value Entry (Table 5802)')
pull_vendor_ledger  = _blocked('Vendor Ledger',   'Vendor Ledger Entry (Table 25)')
pull_return_ship    = _blocked('Return Shipments','Return Shipment Header + Line (Tables 6650, 6651)')
pull_observed_lt    = _blocked('Observed LT',
                               'Posted Purch Rcpt Header+Line (120, 121) AND '
                               'Purchase Header Archive+Line Archive (5107, 5110)')
pull_purchase_orders_blocked = _blocked('Purchase Orders',
                               'PO header+line join needs verification against SSMS schema')


# Endpoint registry: name → (function, status)
ENDPOINTS = {
    'locations':       (pull_locations,        'LIVE'),
    'items':           (pull_items,            'LIVE'),
    'qoh':             (pull_qoh_from_sku,     'LIVE'),
    'customers':       (pull_customers,        'LIVE'),
    'ile':             (pull_ile,              'LIVE'),
    'purchase_orders': (pull_purchase_orders_blocked, 'TODO (endpoint live, mapping TODO)'),
    'vendors':         (pull_vendors,          'BLOCKED'),
    'item_vendor':     (pull_item_vendor,      'BLOCKED'),
    'sales_lines':     (pull_sales_lines,      'BLOCKED'),
    'value_entries':   (pull_value_entries,    'BLOCKED'),
    'vendor_ledger':   (pull_vendor_ledger,    'BLOCKED'),
    'return_shipments':(pull_return_ship,      'BLOCKED'),
    'observed_lt':     (pull_observed_lt,      'BLOCKED'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('endpoints', nargs='*', help='specific endpoints to pull (default: all live)')
    ap.add_argument('--list', action='store_true', help='list endpoints and their status')
    args = ap.parse_args()

    if args.list:
        print(f"{'Endpoint':<20s} Status")
        print(f"{'-' * 20} {'-' * 40}")
        for name, (_, status) in ENDPOINTS.items():
            print(f"{name:<20s} {status}")
        return

    if args.endpoints:
        unknown = [e for e in args.endpoints if e not in ENDPOINTS]
        if unknown:
            sys.exit(f"Unknown endpoints: {unknown}\nKnown: {', '.join(ENDPOINTS)}")
        targets = args.endpoints
    else:
        # default: pull only LIVE endpoints
        targets = [n for n, (_, s) in ENDPOINTS.items() if s == 'LIVE']

    print(f"BC pull → {EXPORTS_DIR}")
    print(f"Endpoints: {', '.join(targets)}\n")
    s = session()
    started = time.time()
    for name in targets:
        fn, status = ENDPOINTS[name]
        print(f"[{status}] {name}")
        try:
            fn(s)
        except Exception as e:
            print(f"      FAILED: {type(e).__name__}: {e}")
        print()
    print(f"Done in {time.time()-started:.1f}s")


if __name__ == '__main__':
    main()
