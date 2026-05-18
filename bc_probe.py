"""
BC OData4 Probe — schema discovery, not part of the running app

Hits each configured BC OData4 endpoint with $top=1, prints:
  - HTTP status
  - field names from the first record
  - a few sample values

Auth: Windows Integrated (NTLM/Negotiate) via requests-negotiate-sspi.
Must run on a domain-joined Windows machine on the Indelco LAN.

Setup (one-time):
  pip install requests requests-negotiate-sspi

Usage:
  python bc_probe.py              # probe all endpoints
  python bc_probe.py locations    # probe just one (key from ENDPOINTS below)
"""

import sys
import json

ENDPOINTS = {
    'locations':   "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/Locations",
    'items':       "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/ItemList",
    'sku':         "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/SKU",
    'sales_order': "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/SalesOrder",
    'sales_line':  "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/SalesLine",
    'ile':         "http://Indelco-BC2:9048/BC270PROD/ODataV4/Company('Indelco%20Plastics')/ILE",
}

try:
    import requests
    from requests_negotiate_sspi import HttpNegotiateAuth
except ImportError as e:
    print(f"Missing dependency: {e.name}")
    print("Install with:  pip install requests requests-negotiate-sspi")
    sys.exit(1)


def probe(name, base_url):
    url = f"{base_url}?$top=1"
    print(f"\n{'=' * 70}")
    print(f"[{name}]  {url}")
    print('=' * 70)
    try:
        r = requests.get(url, auth=HttpNegotiateAuth(), timeout=15)
    except Exception as e:
        print(f"  REQUEST FAILED: {type(e).__name__}: {e}")
        return

    print(f"  HTTP {r.status_code}  ({len(r.content)} bytes)")
    if r.status_code != 200:
        print(f"  body: {r.text[:500]}")
        return

    try:
        body = r.json()
    except ValueError:
        print(f"  non-JSON body: {r.text[:300]}")
        return

    rows = body.get('value', [])
    if not rows:
        print("  empty result set (table is empty or filter excluded everything)")
        return

    row = rows[0]
    print(f"  fields ({len(row)}):")
    for k, v in row.items():
        sample = repr(v)
        if len(sample) > 60:
            sample = sample[:57] + '...'
        print(f"    {k:40s}  {sample}")


def main():
    targets = ENDPOINTS
    if len(sys.argv) > 1:
        key = sys.argv[1]
        if key not in ENDPOINTS:
            print(f"Unknown endpoint '{key}'. Known: {', '.join(ENDPOINTS)}")
            sys.exit(1)
        targets = {key: ENDPOINTS[key]}

    for name, url in targets.items():
        probe(name, url)

    print(f"\n{'=' * 70}\nDone.\n")


if __name__ == '__main__':
    main()
