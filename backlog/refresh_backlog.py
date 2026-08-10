"""
Backlog Review — daily refresh from Business Central (OAuth + OData V4).

Pulls the 4 reports (Sales Lines primary; Purchase Lines/Orders for the
"On PO" status), enriches to canonical order lines, and upserts them into
Supabase bl_source_lines. The Backlog Review app reads from there — no
manual CSV drop.

Run on a LAN machine that can reach BC (scheduled task or ad-hoc):
  pip install requests
  python refresh_backlog.py --probe     # dump each entity's fields (no writes)
  python refresh_backlog.py             # full refresh

Config: copy refresh_config.example.json -> refresh_config.local.json and
fill it in. The OAuth client_secret lives ONLY in that gitignored file.

Field names below are best guesses from the workbook's Source_Sales_Lines.
Run --probe first and adjust FIELD_MAP to the actual OData field names.
"""

import sys, json, argparse, urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests"); sys.exit(1)

HERE = Path(__file__).parent
CONFIG = HERE / 'refresh_config.local.json'

# canonical field -> BC OData field name (adjust after --probe)
FIELD_MAP = {
    'document_no':          'Document_No',
    'line_no':              'Line_No',
    'customer_no':          'Sell_to_Customer_No',
    'location_code':        'Location_Code',
    'branch_name':          'Branch_Name',
    'salesperson':          'Salesperson',
    'buyer_name':           'Buyer_Name',
    'vendor_no':            'Vendor_No',
    'vendor_name':          'Vendor_Name',
    'item_no':              'No',
    'description':          'Description',
    'quantity':             'Quantity',
    'outstanding_quantity': 'Outstanding_Quantity',
    'uom':                  'Unit_of_Measure_Code',
    'outstanding_amount':   'Outstanding_Amount',
    'shipment_date':        'Shipment_Date',
    'order_date':           'Order_Date',
    'qty_on_hand':          'Quantity_on_Hand',
    'is_drop_ship':         'Is_DS',
}


def load_config():
    if not CONFIG.exists():
        sys.exit(f"Missing {CONFIG.name}. Copy refresh_config.example.json to it and fill it in.")
    return json.loads(CONFIG.read_text())


def get_token(bc):
    r = requests.post(bc['oauth_token_url'], data={
        'grant_type': 'client_credentials',
        'client_id': bc['client_id'],
        'client_secret': bc['client_secret'],
        'scope': bc['scope'],
    }, timeout=30)
    if r.status_code != 200:
        sys.exit(f"OAuth token failed: {r.status_code} {r.text[:300]}")
    return r.json()['access_token']


def entity_url(bc, entity):
    comp = urllib.parse.quote(f"Company('{bc['company']}')")
    return f"{bc['odata_base']}/{comp}/{entity}"


def fetch_all(bc, token, entity, params=None):
    """Walk @odata.nextLink paging; yield rows."""
    url = entity_url(bc, entity)
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    first = True
    while url:
        r = requests.get(url, headers=headers, params=params if first else None, timeout=120)
        if r.status_code != 200:
            sys.exit(f"  {entity} HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        for row in body.get('value', []):
            yield row
        url = body.get('@odata.nextLink'); first = False


def probe(cfg):
    """Fetch one row from each configured entity and print its fields."""
    bc = cfg['bc']; token = get_token(bc)
    for name, ent in bc['entities'].items():
        print(f"\n=== {name} -> {ent} ===")
        rows = list(fetch_all(bc, token, ent, {'$top': '1'}))
        if not rows:
            print("  (no rows)"); continue
        for k, v in rows[0].items():
            print(f"  {k:32s} {repr(v)[:50]}")


def g(row, canon):
    return row.get(FIELD_MAP.get(canon, canon))


def classify(qoh, out_qty, on_po):
    """v1 line status (mirrors the workbook's Shortage column). Refine with
    the real netting once we see live data."""
    try: qoh = float(qoh or 0); out_qty = float(out_qty or 0)
    except (TypeError, ValueError): qoh = out_qty = 0
    if qoh >= out_qty and out_qty > 0:
        return 'In Stock; Ship'
    if on_po:
        return 'On PO'
    if out_qty > 0:
        return 'Needs PO'
    return 'None'


def refresh(cfg):
    bc = cfg['bc']; token = get_token(bc)
    src = cfg.get('source_system', 'INDELCO_BC')

    # 1) open items that are on a purchase order — keyed by item+location,
    #    used to mark "On PO". Pull open purchase lines.
    on_po = set()
    try:
        for pl in fetch_all(bc, token, bc['entities']['purchase_lines'],
                            {'$filter': "Outstanding_Quantity gt 0"}):
            key = (str(pl.get('No', '')).strip(), str(pl.get('Location_Code', '')).strip())
            on_po.add(key)
        print(f"  open PO item/locations: {len(on_po):,}")
    except Exception as e:
        print(f"  (purchase lines skipped: {e})")

    # 2) open sales lines -> canonical rows
    rows = []
    n = 0
    for sl in fetch_all(bc, token, bc['entities']['sales_lines'],
                        {'$filter': "Document_Type eq 'Order' and Outstanding_Quantity gt 0"}):
        item = str(g(sl, 'item_no') or '').strip()
        loc = str(g(sl, 'location_code') or '').strip()
        status = classify(g(sl, 'qty_on_hand'), g(sl, 'outstanding_quantity'),
                          (item, loc) in on_po)
        rows.append({
            'source_system': src,
            'document_no': str(g(sl, 'document_no') or '').strip(),
            'line_no': int(g(sl, 'line_no') or 0),
            'customer_no': g(sl, 'customer_no'),
            'location_code': loc,
            'branch_name': g(sl, 'branch_name'),
            'salesperson': g(sl, 'salesperson'),
            'buyer_name': g(sl, 'buyer_name'),
            'vendor_no': g(sl, 'vendor_no'),
            'vendor_name': g(sl, 'vendor_name'),
            'item_no': item,
            'description': g(sl, 'description'),
            'quantity': g(sl, 'quantity'),
            'outstanding_quantity': g(sl, 'outstanding_quantity'),
            'uom': g(sl, 'uom'),
            'outstanding_amount': g(sl, 'outstanding_amount'),
            'shipment_date': (str(g(sl, 'shipment_date'))[:10] or None) if g(sl, 'shipment_date') else None,
            'order_date': (str(g(sl, 'order_date'))[:10] or None) if g(sl, 'order_date') else None,
            'qty_on_hand': g(sl, 'qty_on_hand'),
            'status': status,
            'is_drop_ship': bool(g(sl, 'is_drop_ship')),
        })
        n += 1
    print(f"  open sales lines: {n:,}")

    push_supabase(cfg, src, rows)


def push_supabase(cfg, src, rows):
    sb = cfg['supabase']
    h = {'apikey': sb['key'], 'Authorization': 'Bearer ' + sb['key'],
         'Content-Type': 'application/json'}
    base = f"{sb['url']}/rest/v1/bl_source_lines"
    # replace this source's rows: delete then bulk insert (fresh open set)
    d = requests.delete(f"{base}?source_system=eq.{urllib.parse.quote(src)}",
                        headers={**h, 'Prefer': 'return=minimal'}, timeout=60)
    print(f"  cleared old rows: HTTP {d.status_code}")
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        r = requests.post(base, headers={**h, 'Prefer': 'return=minimal'},
                          data=json.dumps(chunk), timeout=120)
        if r.status_code >= 300:
            sys.exit(f"  insert failed at {i}: {r.status_code} {r.text[:300]}")
    print(f"  pushed {len(rows):,} rows to bl_source_lines")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='dump entity fields, no writes')
    args = ap.parse_args()
    cfg = load_config()
    if args.probe:
        probe(cfg)
    else:
        refresh(cfg)
        print("Done.")


if __name__ == '__main__':
    main()
