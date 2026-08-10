"""
Backlog Review — daily refresh from Business Central (on-prem OData V4).

Auth is Windows Integrated (NTLM) by default — the same mechanism the I3
bc_pull.py uses. No OAuth, no client id/secret, no typed password: it uses
your logged-in Windows session. Must run on a domain machine that can
reach BC (e.g. indelco-bc2), same as the I3 pull.

Pulls Sales Lines (has QoH by item/location), enriches to canonical order
lines, and upserts them into Supabase bl_source_lines. The app reads from
there. Purchase Lines is optional (only adds On-PO vs Needs-PO).

Setup on a domain machine:
  pip install requests requests-negotiate-sspi
  copy refresh_config.example.json -> refresh_config.local.json  and paste
  your Sales Lines OData URL into it.
  python refresh_backlog.py --probe     # dump fields, no writes
  python refresh_backlog.py             # full refresh
"""

import sys, json, argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests requests-negotiate-sspi"); sys.exit(1)

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
        sys.exit(f"Missing {CONFIG.name}. Copy refresh_config.example.json to it and paste your Sales Lines OData URL.")
    return json.loads(CONFIG.read_text())


def get_session(cfg):
    """Windows/NTLM by default (uses your logged-in session). Set
    "auth":"oauth" in the config only if you ever move to BC cloud."""
    s = requests.Session()
    auth = cfg.get('auth', 'windows')
    if auth == 'windows':
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth
        except ImportError:
            sys.exit("Windows auth needs:  pip install requests-negotiate-sspi")
        s.auth = HttpNegotiateAuth()
    elif auth == 'oauth':
        o = cfg['oauth']
        r = requests.post(o['token_url'], data={
            'grant_type': 'client_credentials', 'client_id': o['client_id'],
            'client_secret': o['client_secret'], 'scope': o['scope']}, timeout=30)
        if r.status_code != 200:
            sys.exit(f"OAuth token failed: {r.status_code} {r.text[:300]}")
        s.headers['Authorization'] = 'Bearer ' + r.json()['access_token']
    return s


def fetch_all(s, url, params=None):
    """Walk @odata.nextLink paging from a full entity URL."""
    first = True
    while url:
        r = s.get(url, params=params if first else None,
                  headers={'Accept': 'application/json'}, timeout=180)
        if r.status_code != 200:
            sys.exit(f"  HTTP {r.status_code} on {url}\n  {r.text[:300]}")
        body = r.json()
        for row in body.get('value', []):
            yield row
        url = body.get('@odata.nextLink'); first = False


def probe(cfg):
    s = get_session(cfg)
    for name, url in cfg['entities'].items():
        if not url or url.startswith('PASTE'):
            continue
        print(f"\n=== {name} ===\n{url}")
        rows = list(fetch_all(s, url, {'$top': '1'}))
        if not rows:
            print("  (no rows)"); continue
        for k, v in rows[0].items():
            print(f"  {k:34s} {repr(v)[:48]}")


def g(row, canon):
    return row.get(FIELD_MAP.get(canon, canon))


def numf(v):
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0


def classify(qoh, out_qty, on_po):
    """v1 line status, mirrors the workbook's Shortage column. Refine once
    we validate against live data."""
    qoh, out_qty = numf(qoh), numf(out_qty)
    if out_qty > 0 and qoh >= out_qty:
        return 'In Stock; Ship'
    if on_po:
        return 'On PO'
    if out_qty > 0:
        return 'Needs PO'
    return 'None'


def refresh(cfg):
    s = get_session(cfg)
    src = cfg.get('source_system', 'INDELCO_BC')
    ents = cfg['entities']

    # optional: open purchase lines -> which (item, location) are on a PO
    on_po = set()
    plurl = ents.get('purchase_lines')
    if plurl and not plurl.startswith('PASTE'):
        try:
            for pl in fetch_all(s, plurl):
                if numf(pl.get('Outstanding_Quantity')) > 0:
                    on_po.add((str(pl.get('No', '')).strip(), str(pl.get('Location_Code', '')).strip()))
            print(f"  open PO item/locations: {len(on_po):,}")
        except Exception as e:
            print(f"  (purchase lines skipped: {e})")

    rows = []
    for sl in fetch_all(s, ents['sales_lines']):
        if numf(g(sl, 'outstanding_quantity')) <= 0:
            continue   # open lines only (robust filter in Python)
        item = str(g(sl, 'item_no') or '').strip()
        loc = str(g(sl, 'location_code') or '').strip()
        sd = g(sl, 'shipment_date'); od = g(sl, 'order_date')
        rows.append({
            'source_system': src,
            'document_no': str(g(sl, 'document_no') or '').strip(),
            'line_no': int(numf(g(sl, 'line_no'))),
            'customer_no': g(sl, 'customer_no'), 'location_code': loc,
            'branch_name': g(sl, 'branch_name'), 'salesperson': g(sl, 'salesperson'),
            'buyer_name': g(sl, 'buyer_name'),
            'vendor_no': g(sl, 'vendor_no'), 'vendor_name': g(sl, 'vendor_name'),
            'item_no': item, 'description': g(sl, 'description'),
            'quantity': numf(g(sl, 'quantity')),
            'outstanding_quantity': numf(g(sl, 'outstanding_quantity')),
            'uom': g(sl, 'uom'), 'outstanding_amount': numf(g(sl, 'outstanding_amount')),
            'shipment_date': str(sd)[:10] if sd else None,
            'order_date': str(od)[:10] if od else None,
            'qty_on_hand': numf(g(sl, 'qty_on_hand')),
            'status': classify(g(sl, 'qty_on_hand'), g(sl, 'outstanding_quantity'), (item, loc) in on_po),
            'is_drop_ship': bool(g(sl, 'is_drop_ship')),
        })
    print(f"  open sales lines: {len(rows):,}")
    push_supabase(cfg, src, rows)


def push_supabase(cfg, src, rows):
    sb = cfg['supabase']
    h = {'apikey': sb['key'], 'Authorization': 'Bearer ' + sb['key'], 'Content-Type': 'application/json'}
    base = f"{sb['url']}/rest/v1/bl_source_lines"
    import urllib.parse
    d = requests.delete(f"{base}?source_system=eq.{urllib.parse.quote(src)}",
                        headers={**h, 'Prefer': 'return=minimal'}, timeout=60)
    print(f"  cleared old rows: HTTP {d.status_code}")
    for i in range(0, len(rows), 500):
        r = requests.post(base, headers={**h, 'Prefer': 'return=minimal'},
                          data=json.dumps(rows[i:i+500]), timeout=120)
        if r.status_code >= 300:
            sys.exit(f"  insert failed at {i}: {r.status_code} {r.text[:300]}")
    print(f"  pushed {len(rows):,} rows to bl_source_lines")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='dump entity fields, no writes')
    args = ap.parse_args()
    cfg = load_config()
    probe(cfg) if args.probe else (refresh(cfg), print("Done."))


if __name__ == '__main__':
    main()
