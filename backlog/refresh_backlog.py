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
  pip install requests requests-negotiate-sspi truststore
  copy refresh_config.example.json -> refresh_config.local.json  and paste
  your Sales Lines OData URL into it.
  python refresh_backlog.py --probe     # dump fields, no writes
  python refresh_backlog.py             # full refresh

truststore makes Python trust the Windows certificate store. On a corporate
network that does SSL inspection, HTTPS to Supabase is re-signed by a company
root CA that Python's bundled certifi doesn't know — but Windows does. Without
it you get "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate".
"""

import sys, json, time, argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests requests-negotiate-sspi truststore"); sys.exit(1)


def enable_os_trust():
    """Route TLS verification through the OS (Windows) trust store so a
    corporate SSL-inspection root CA is trusted. BC is plain HTTP so this
    only matters for the HTTPS push to Supabase. Best-effort: if truststore
    isn't installed we carry on and let the SSL error (with a hint) surface."""
    try:
        import truststore
        truststore.inject_into_ssl()
        return True
    except Exception:
        return False

HERE = Path(__file__).parent
CONFIG = HERE / 'refresh_config.local.json'

# Sales LINE fields (confirmed from the live probe)
LINE_MAP = {
    'document_type':        'Document_Type',
    'document_no':          'Document_No',
    'line_no':              'Line_No',
    'customer_no':          'Sell_to_Customer_No',
    'location_code':        'Location_Code',
    'item_no':              'No',
    'description':          'Description',
    'quantity':             'Quantity',
    'outstanding_quantity': 'Outstanding_Quantity',
    'uom':                  'Unit_of_Measure_Code',
    'line_amount':          'Line_Amount',        # full line $; outstanding $ derived
    'shipment_date':        'Shipment_Date',
    'qty_on_hand':          'Inventory',          # QoH by item/location
    'is_drop_ship':         'Drop_Shipment',
}
# Sales HEADER (Sales Orders) fields — joined on document no to get the
# salesperson (DOMAIN\user), order date, customer name. Confirm/adjust the
# salesperson field after probing the Sales Orders webservice.
HDR_DOCNO = 'No'
HDR_MAP = {
    # DOMAIN\user review owner. From the live probe, Created_By holds it
    # (Assigned_User_ID was blank). Override via salesperson_field if needed.
    'salesperson':    'Created_By',
    'order_date':     'Order_Date',
    'customer_name':  'Sell_to_Customer_Name',
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


def _get(s, url, params, timeout=300, tries=4, fatal=True):
    """GET with retry/backoff — BC pages can be slow to render server-side.
    Returns the response, or None on a non-200 when fatal=False."""
    for attempt in range(tries):
        try:
            r = s.get(url, params=params, headers={'Accept': 'application/json'},
                      timeout=timeout)
            if r.status_code != 200:
                if not fatal:
                    return r
                sys.exit(f"  HTTP {r.status_code} on {url}\n  {r.text[:300]}")
            return r
        except requests.exceptions.RequestException as e:
            if attempt == tries - 1:
                raise
            wait = 2 ** (attempt + 1)   # 2s, 4s, 8s
            print(f"  ...retry {attempt+1}/{tries-1} after {wait}s ({type(e).__name__})")
            time.sleep(wait)


def usable_params(s, url, params):
    """Probe with $top=1 to confirm the server accepts these $select/$filter
    options. If it 400s (a web service that won't filter/select those fields),
    drop the options so the pull still works — just wider/longer."""
    probe_params = {**params, '$top': '1'}
    r = _get(s, url, probe_params, timeout=120, tries=2, fatal=False)
    if r is not None and r.status_code == 200:
        return params
    code = r.status_code if r is not None else '???'
    print(f"  (server rejected $select/$filter: HTTP {code} — pulling full rows)")
    return None


def fetch_all(s, url, params=None):
    """Walk @odata.nextLink paging from a full entity URL. $select/$filter
    (in params) keep the first-page payload small; nextLink carries them on."""
    first = True
    while url:
        r = _get(s, url, params if first else None)
        body = r.json()
        for row in body.get('value', []):
            yield row
        url = body.get('@odata.nextLink'); first = False


def _window(s, url, base_params, skip, count, skipped):
    """Read [skip, skip+count) as one page. If the API page refuses to
    serialize it (BC throws Application_FieldValidationException on certain
    records regardless of status), bisect down to isolate and skip just the
    poison row(s) instead of losing the whole page. Returns (rows, at_end)
    where at_end is True only when a successful read returned fewer rows than
    requested — i.e. we've reached the end of the data (NOT merely short
    because poison rows were dropped, which must not stop paging)."""
    params = {**base_params, '$top': str(count), '$skip': str(skip)}
    r = _get(s, url, params, timeout=300, tries=3, fatal=False)
    if r is not None and r.status_code == 200:
        rows = r.json().get('value', [])
        return rows, len(rows) < count
    if count <= 1:
        skipped.append(skip)   # one unreadable record — drop it, keep going
        return [], False       # a 400 here means the record exists; not the end
    half = count // 2
    lrows, _ = _window(s, url, base_params, skip, half, skipped)
    rrows, at_end = _window(s, url, base_params, skip + half, count - half, skipped)
    return lrows + rrows, at_end   # only the tail half decides end-of-data


def fetch_resilient(s, url, base_params, page=2000):
    """Key-ordered $top/$skip paging that survives poison records. Used for
    BC API pages (like SalesOrder) that validate on read; a plain nextLink
    walk dies on the first record the page won't serialize."""
    skip, skipped = 0, []
    while True:
        rows, at_end = _window(s, url, base_params, skip, page, skipped)
        for row in rows:
            yield row
        if at_end:
            break
        skip += page
    if skipped:
        print(f"  (skipped {len(skipped)} unreadable order header(s) the API "
              f"page refused to serialize)")


def is_url(u):
    return bool(u) and str(u).lower().startswith('http')


def probe(cfg):
    s = get_session(cfg)
    for name, url in cfg['entities'].items():
        if not is_url(url):
            print(f"\n=== {name} === (skipped — no URL configured)")
            continue
        print(f"\n=== {name} ===\n{url}")
        rows = list(fetch_all(s, url, {'$top': '1'}))
        if not rows:
            print("  (no rows)"); continue
        for k, v in rows[0].items():
            print(f"  {k:34s} {repr(v)[:48]}")


def g(row, canon):
    return row.get(LINE_MAP.get(canon, canon))


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
    spf = cfg.get('salesperson_field', HDR_MAP['salesperson'])

    # Sales Header (Orders) -> document_no -> {salesperson, order_date}.
    # We only need the fields the Sales Line lacks: salesperson (DOMAIN\user
    # via Created_By) and order date. $select trims the ~170-column entity so
    # the pull is small; fetch_resilient reads status-agnostically and skips
    # any single record the API page refuses to serialize (BC throws a
    # FieldValidationException on certain orders regardless of status).
    hdr_sel = ','.join(dict.fromkeys([HDR_DOCNO, spf, HDR_MAP['order_date']]))
    headers = {}
    sourl = ents.get('sales_orders')
    if is_url(sourl):
        # key-order for stable $skip paging; drop $select/$orderby if the
        # server won't accept them, but keep the pull working either way.
        hdr_params = usable_params(s, sourl, {'$select': hdr_sel, '$orderby': HDR_DOCNO})
        if hdr_params is None:
            hdr_params = {'$orderby': HDR_DOCNO} if usable_params(
                s, sourl, {'$orderby': HDR_DOCNO}) else {}
        for so in fetch_resilient(s, sourl, hdr_params):
            headers[str(so.get(HDR_DOCNO, '')).strip()] = {
                'salesperson': so.get(spf),
                'order_date': so.get(HDR_MAP['order_date']),
            }
        print(f"  sales order headers: {len(headers):,}")
    else:
        print("  (no sales_orders URL — salesperson/order date will be blank)")

    # open purchase lines -> which (item, location) are on a PO
    on_po = set()
    if is_url(ents.get('purchase_lines')):
        try:
            for pl in fetch_all(s, ents['purchase_lines']):
                if numf(pl.get('Outstanding_Quantity')) > 0:
                    on_po.add((str(pl.get('No', '')).strip(), str(pl.get('Location_Code', '')).strip()))
            print(f"  open PO item/locations: {len(on_po):,}")
        except Exception as e:
            print(f"  (purchase lines skipped: {e})")

    # Sales Lines — $select the mapped fields and $filter server-side to open
    # order lines only. This cuts both width (fewer columns) and length (skips
    # quotes and fully-shipped lines) so the pull returns fast.
    line_sel = ','.join(dict.fromkeys(LINE_MAP.values()))
    line_flt = "Document_Type eq 'Order' and Outstanding_Quantity gt 0"
    line_params = usable_params(s, ents['sales_lines'],
                                {'$select': line_sel, '$filter': line_flt})
    rows = []
    for sl in fetch_all(s, ents['sales_lines'], line_params):
        if str(g(sl, 'document_type') or '').strip() != 'Order':
            continue   # orders only (feed also has Quotes)
        oq = numf(g(sl, 'outstanding_quantity'))
        if oq <= 0:
            continue
        item = str(g(sl, 'item_no') or '').strip()
        loc = str(g(sl, 'location_code') or '').strip()
        doc = str(g(sl, 'document_no') or '').strip()
        qty = numf(g(sl, 'quantity'))
        line_amt = numf(g(sl, 'line_amount'))
        out_amt = line_amt * (oq / qty) if qty else line_amt   # open $ portion
        sd = g(sl, 'shipment_date')
        hdr = headers.get(doc, {})
        od = hdr.get('order_date')
        rows.append({
            'source_system': src, 'document_no': doc,
            'line_no': int(numf(g(sl, 'line_no'))),
            'customer_no': g(sl, 'customer_no'), 'location_code': loc,
            'branch_name': None, 'salesperson': hdr.get('salesperson'),
            'buyer_name': None, 'vendor_no': None, 'vendor_name': None,
            'item_no': item, 'description': g(sl, 'description'),
            'quantity': qty, 'outstanding_quantity': oq,
            'uom': g(sl, 'uom'), 'outstanding_amount': round(out_amt, 2),
            'shipment_date': str(sd)[:10] if sd else None,
            'order_date': str(od)[:10] if od else None,
            'qty_on_hand': numf(g(sl, 'qty_on_hand')),
            'status': classify(g(sl, 'qty_on_hand'), oq, (item, loc) in on_po),
            'is_drop_ship': bool(g(sl, 'is_drop_ship')),
        })
    print(f"  open order lines: {len(rows):,}")
    push_supabase(cfg, src, rows)


def push_supabase(cfg, src, rows):
    sb = cfg['supabase']
    h = {'apikey': sb['key'], 'Authorization': 'Bearer ' + sb['key'], 'Content-Type': 'application/json'}
    base = f"{sb['url']}/rest/v1/bl_source_lines"
    import urllib.parse
    try:
        d = requests.delete(f"{base}?source_system=eq.{urllib.parse.quote(src)}",
                            headers={**h, 'Prefer': 'return=minimal'}, timeout=60)
        print(f"  cleared old rows: HTTP {d.status_code}")
        for i in range(0, len(rows), 500):
            r = requests.post(base, headers={**h, 'Prefer': 'return=minimal'},
                              data=json.dumps(rows[i:i+500]), timeout=120)
            if r.status_code >= 300:
                sys.exit(f"  insert failed at {i}: {r.status_code} {r.text[:300]}")
    except requests.exceptions.SSLError:
        sys.exit(
            "  SSL verify to Supabase failed (corporate SSL inspection).\n"
            "  Fix:  pip install truststore   then re-run. It makes Python\n"
            "  trust the Windows certificate store, which has your company CA.")
    print(f"  pushed {len(rows):,} rows to bl_source_lines")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='dump entity fields, no writes')
    args = ap.parse_args()
    if not enable_os_trust():
        print("  (truststore not installed — if the Supabase push fails on SSL,"
              " run: pip install truststore)")
    cfg = load_config()
    probe(cfg) if args.probe else (refresh(cfg), print("Done."))


if __name__ == '__main__':
    main()
