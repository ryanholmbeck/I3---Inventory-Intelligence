"""
Indelco SSMS Connector v4
Database:   BC270PROD
Server:     indelco-bcdb1\\bc
Auth:       Windows (FLUIDFLOW\\RyanHolmbeck)
"""

import sys, csv, argparse
from datetime import datetime
from pathlib import Path

SERVER   = r'indelco-bcdb1\bc'
DATABASE = 'BC270PROD'
GUID     = '437dbf0e-84ff-417a-965d-ed2bb9650972'

COMPANIES = {
    'Indelco Plastics': 'INDELCO',
    'Ayer Sales':       'AYER',
    'Corr Tech':        'CORR',
    'Quality Stainless':'QS',
}
LEGACY_COMPANIES = {'Ayer Sales', 'Corr Tech', 'Quality Stainless'}

OUTPUT_DIR = Path(r'C:\Users\Ryanh\OneDrive - Flow Control Group\3I\SSMS_Exports')


def tbl(company, table_name):
    return f"[{company}${table_name}${GUID}]"


def find_driver():
    import pyodbc
    for d in ['ODBC Driver 18 for SQL Server',
              'ODBC Driver 17 for SQL Server',
              'ODBC Driver 13 for SQL Server',
              'SQL Server Native Client 11.0',
              'SQL Server']:
        if d in pyodbc.drivers():
            return d
    return None


def check_prerequisites():
    try:
        import pyodbc
    except ImportError:
        print("ERROR: run:  pip install pyodbc")
        return False
    driver = find_driver()
    if not driver:
        print("ERROR: No SQL Server ODBC driver found.")
        print("  Install from: https://aka.ms/downloadmsodbcsql")
        return False
    print(f"  Driver: {driver}")
    return True


def get_conn():
    import pyodbc
    driver = find_driver()
    # Try 1: Windows Authentication (works on-site / domain-joined)
    conn_str = (
        f'DRIVER={{{driver}}};'
        f'SERVER={SERVER};'
        f'DATABASE={DATABASE};'
        f'Trusted_Connection=yes;'
        f'TrustServerCertificate=yes;'
        f'Encrypt=Optional;'
    )
    try:
        return pyodbc.connect(conn_str, timeout=10)
    except Exception:
        pass

    # Try 2: SQL Server Authentication (works over VPN from home)
    # Set SQL_USER and SQL_PASS environment variables, or enter below
    import os
    sql_user = os.environ.get('SQL_USER', '')
    sql_pass = os.environ.get('SQL_PASS', '')
    if not sql_user:
        import getpass
        print("  Windows auth failed. Enter SQL Server credentials:")
        sql_user = input("  SQL Username: ").strip()
        sql_pass = getpass.getpass("  SQL Password: ")
    conn_str2 = (
        f'DRIVER={{{driver}}};'
        f'SERVER={SERVER};'
        f'DATABASE={DATABASE};'
        f'UID={sql_user};'
        f'PWD={sql_pass};'
        f'TrustServerCertificate=yes;'
        f'Encrypt=Optional;'
    )
    return pyodbc.connect(conn_str2, timeout=30)


def test_connection():
    print("Checking prerequisites...")
    if not check_prerequisites():
        return False
    print(f"Connecting to {SERVER} / {DATABASE}...")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT @@VERSION, DB_NAME()")
        row = cur.fetchone()
        print(f"  Connected to: {row[1]}")
        print(f"  SQL Server:   {str(row[0])[:80]}")
        cur.execute("""
            SELECT SUBSTRING(name,1,CHARINDEX('$',name)-1) AS co, COUNT(*) AS n
            FROM sys.tables WHERE name LIKE '%$Item Ledger Entry$%'
            GROUP BY SUBSTRING(name,1,CHARINDEX('$',name)-1)
            ORDER BY co
        """)
        print("  Companies:")
        for r in cur.fetchall():
            print(f"    - {r[0]}")
        conn.close()
        print("\nConnection successful.")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


# ── Item Master ───────────────────────────────────────────────────────
# Confirmed columns from INFORMATION_SCHEMA query
def pull_items(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Items [{company}]...")
    sql = f"""
    SELECT
        [No_]                           AS [Item No.],
        [Description],
        [Description 2],
        [Base Unit of Measure],
        [Unit Cost],
        [Standard Cost],
        [Unit Price],
        [Vendor No_]                    AS [Vendor No.],
        [Blocked],
        [Lead Time Calculation],
        [Item Category Code],
        CONVERT(varchar(10), [Last Date Modified], 120) AS [Last Date Modified],
        [Inventory Posting Group],
        [Reordering Policy],
        [Reorder Point],
        [Safety Stock Quantity],
        [Minimum Order Quantity],
        [Maximum Order Quantity],
        [Reorder Quantity],
        [Maximum Inventory],
        [Order Multiple],
        [Costing Method],
        [Purchasing Code],
        [Sales Blocked],
        [Purchasing Blocked]
    FROM {tbl(company, 'Item')}
    WHERE [Blocked] = 0
    ORDER BY [No_]
    """
    return _to_csv(conn, sql, out / f'Items_{short}_{_today()}.csv')


# ── Item Ledger Entry ─────────────────────────────────────────────────
# Confirmed columns — cost data not in ILE in this BC version
# Using Entry Type codes: 0=Purchase, 1=Sale, 2=Positive Adj, 3=Negative Adj,
#                         4=Transfer, 5=Consumption, 6=Output
def pull_ile(conn, company='Indelco Plastics', years_back=4, out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    is_legacy = company in LEGACY_COMPANIES
    date_filter = (
        '' if is_legacy
        else f"WHERE [Posting Date] >= DATEADD(year, -{years_back}, GETDATE())"
    )
    label = 'all history' if is_legacy else f'last {years_back} years'
    print(f"  ILE [{company}] ({label})...")
    sql = f"""
    SELECT
        CONVERT(varchar(10), [Posting Date], 120)   AS [Posting Date],
        CASE [Entry Type]
            WHEN 0 THEN 'Purchase'
            WHEN 1 THEN 'Sale'
            WHEN 2 THEN 'Positive Adjmt.'
            WHEN 3 THEN 'Negative Adjmt.'
            WHEN 4 THEN 'Transfer'
            WHEN 5 THEN 'Consumption'
            WHEN 6 THEN 'Output'
            WHEN 7 THEN 'Assembly Consumption'
            WHEN 8 THEN 'Assembly Output'
            ELSE CAST([Entry Type] AS VARCHAR(20))
        END                             AS [Entry Type],
        [Document Type],
        [Document No_]              AS [Document No.],
        [Item No_]                  AS [Item No.],
        [Description],
        [Location Code],
        [Quantity],
        [Invoiced Quantity],
        [Remaining Quantity],
        [Source No_]                AS [Source No.],
        [Source Type],
        [Entry No_]                 AS [Entry No.],
        [Drop Shipment],
        [Document Line No_],
        [Order Type],
        [Order No_],
        [Variant Code],
        [Global Dimension 1 Code]   AS [Branch Code],
        [Global Dimension 2 Code],
        '{short}'                   AS [Company Source]
    FROM {tbl(company, 'Item Ledger Entry')}
    {date_filter}
    ORDER BY [Posting Date] DESC
    """
    return _to_csv(conn, sql, out / f'ILE_{short}_{_today()}.csv')


# ── Value Entry (cost data lives here in BC, not ILE) ────────────────
def pull_value_entries(conn, company='Indelco Plastics', years_back=4, out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    is_legacy = company in LEGACY_COMPANIES
    date_filter = (
        '' if is_legacy
        else f"WHERE [Posting Date] >= DATEADD(year, -{years_back}, GETDATE())"
    )
    label = 'all history' if is_legacy else f'last {years_back} years'
    print(f"  Value Entries [{company}] ({label})...")
    sql = f"""
    SELECT
        CONVERT(varchar(10), [Posting Date], 120)       AS [Posting Date],
        [Entry No_]                                     AS [Entry No.],
        [Item No_]                                      AS [Item No.],
        [Item Ledger Entry No_]                         AS [ILE Entry No.],
        [Item Ledger Entry Type]                        AS [Entry Type],
        [Document No_]                                  AS [Document No.],
        [Description],
        [Location Code],
        [Invoiced Quantity],
        [Valued Quantity],
        [Cost Amount (Actual)],
        [Cost Amount (Expected)],
        [Sales Amount (Actual)],
        [Sales Amount (Expected)],
        [Cost per Unit],
        [Cost Posted to G_L] AS [Cost Posted to GL],
        [Source No_]                                    AS [Source No.],
        [Source Type],
        [Global Dimension 1 Code]                       AS [Branch Code]
    FROM {tbl(company, 'Value Entry')}
    {date_filter}
    ORDER BY [Posting Date] DESC
    """
    return _to_csv(conn, sql, out / f'ValueEntry_{short}_{_today()}.csv')


# ── Purchase Orders ───────────────────────────────────────────────────
def pull_purchase_orders(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Purchase Orders [{company}]...")
    sql = f"""
    SELECT
        ph.[No_]                                        AS [PO No.],
        ph.[Buy-from Vendor No_]                        AS [Vendor No.],
        ph.[Buy-from Vendor Name]                       AS [Vendor Name],
        CONVERT(varchar(10), ph.[Order Date], 120)      AS [Order Date],
        CONVERT(varchar(10), ph.[Expected Receipt Date], 120) AS [Expected Receipt Date],
        ph.[Status],
        ph.[Location Code],
        pl.[Completely Received],
        pl.[Line No_]                                   AS [Line No.],
        pl.[No_]                                        AS [Item No.],
        pl.[Description],
        pl.[Quantity],
        pl.[Quantity Received],
        pl.[Outstanding Quantity],
        COALESCE(NULLIF(pl.[Direct Unit Cost],0), pl.[Unit Cost (LCY)], 0) AS [Unit Cost],
        pl.[Drop Shipment]                              AS [Drop Shipment],
        ph.[Purchaser Code]                             AS [Purchaser Code],
        pl.[Item Category Code],
        pl.[Qty_ Rcd_ Not Invoiced]                     AS [Qty Rcd Not Invoiced],
        -- Promised date: Expected Receipt Date if valid, else Requested Receipt Date if valid, else NULL
        CASE
            WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
            WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
            ELSE NULL
        END                                             AS [Promised Date],
        CASE
            WHEN pl.[Outstanding Quantity] > 0
             AND CASE
                   WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                   WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                   ELSE NULL
                 END < GETDATE()
            THEN 1 ELSE 0
        END                                             AS [Is Late],
        CASE
            WHEN CASE
                   WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                   WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                   ELSE NULL
                 END IS NOT NULL
             AND CASE
                   WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                   WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                   ELSE NULL
                 END < GETDATE()
            THEN DATEDIFF(day,
                   CASE
                     WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                     WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                     ELSE NULL
                   END, GETDATE())
            ELSE 0
        END                                             AS [Days Late],
        CASE
            WHEN ISNULL(ph.[Vendor Order No_], '') = ''
            THEN 1 ELSE 0
        END                                             AS [Unconfirmed],
        pl.[Quantity Received] - pl.[Quantity Invoiced] AS [Qty RNI],
        (pl.[Quantity Received] - pl.[Quantity Invoiced])
            * pl.[Direct Unit Cost]                     AS [RNI Value]
    FROM {tbl(company, 'Purchase Header')} ph
    JOIN {tbl(company, 'Purchase Line')} pl
        ON  pl.[Document No_]  = ph.[No_]
        AND pl.[Document Type] = ph.[Document Type]
    WHERE ph.[Document Type] = 1
      AND ph.[Order Date]    >= DATEADD(year, -2, GETDATE())
      AND pl.[Type]          = 2
    ORDER BY ph.[Order Date] DESC
    """
    return _to_csv(conn, sql, out / f'PO_{short}_{_today()}.csv')


# ── Vendor Ledger ─────────────────────────────────────────────────────
def pull_vendor_ledger(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Vendor Ledger [{company}]...")
    sql = f"""
    SELECT
        [Vendor No_]                AS [Vendor No.],
        CONVERT(varchar(10), [Posting Date], 120) AS [Posting Date],
        CONVERT(varchar(10), [Due Date], 120)     AS [Due Date],
        [Document Type],
        [Document No_]              AS [Document No.],
        [Description],
        [Purchase (LCY)]            AS [Amount],
        [Remaining Amt_ (LCY)]      AS [Remaining Amount],
        [Open]
    FROM {tbl(company, 'Vendor Ledger Entry')}
    WHERE [Posting Date] >= DATEADD(year, -2, GETDATE())
      AND [Document Type] = 2
    ORDER BY [Posting Date] DESC
    """
    return _to_csv(conn, sql, out / f'VendorLedger_{short}_{_today()}.csv')


# ── QoH ───────────────────────────────────────────────────────────────
def pull_qoh(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Qty on Hand [{company}]...")
    sql = f"""
    SELECT
        [Item No_]         AS [Item No.],
        [Location Code],
        SUM([Quantity])    AS [Qty on Hand]
    FROM {tbl(company, 'Item Ledger Entry')}
    GROUP BY [Item No_], [Location Code]
    HAVING SUM([Quantity]) <> 0
    ORDER BY [Item No_], [Location Code]
    """
    return _to_csv(conn, sql, out / f'QoH_{short}_{_today()}.csv')


# ── Locations ─────────────────────────────────────────────────────────
def pull_locations(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Locations [{company}]...")
    sql = f"""
    SELECT [Code], [Name], [Name 2], [City], [County]
    FROM {tbl(company, 'Location')}
    ORDER BY [Code]
    """
    return _to_csv(conn, sql, out / f'Locations_{short}_{_today()}.csv')


# ── CSV writer ────────────────────────────────────────────────────────
def _to_csv(conn, sql, filepath):
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)
        print(f"    {len(rows):,} rows -> {filepath.name}")
        return filepath
    except Exception as e:
        print(f"    FAILED: {e}")
        raise


def _today():
    return datetime.now().strftime('%Y%m%d')


# ── CLI ───────────────────────────────────────────────────────────────

def pull_customers(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    """Customer master — name lookup for spike log and demand planning"""
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Customers [{company}]...")
    sql = f"""
    SELECT
        [No_]                           AS [Customer No.],
        [Name],
        [Name 2],
        [Address],
        [City],
        [State],
        [Post Code]                     AS [Zip],
        [Country_Region Code]           AS [Country],
        [Phone No_]                     AS [Phone],
        [E-Mail]                        AS [Email],
        [Customer Posting Group],
        [Customer Price Group],
        [Salesperson Code],
        [Payment Terms Code],
        [Credit Limit (LCY)]            AS [Credit Limit],
        [Blocked]
    FROM {tbl(company, 'Customer')}
    WHERE [Blocked] = 0
    ORDER BY [No_]
    """
    try:
        return _to_csv(conn, sql, out / f'Customers_{short}_{_today()}.csv')
    except Exception as e:
        print(f"    FAILED: {e}")


def pull_vendors(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    """Vendor master — for scorecard and RFQ"""
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Vendors [{company}]...")
    sql = f"""
    SELECT
        [No_]                           AS [Vendor No.],
        [Name],
        [Name 2],
        [Address],
        [City],
        [State],
        [Post Code]                     AS [Zip],
        [Country_Region Code]           AS [Country],
        [Phone No_]                     AS [Phone],
        [E-Mail]                        AS [Email],
        [Vendor Posting Group],
        [Payment Terms Code],
        [Currency Code],
        [Lead Time Calculation]         AS [Lead Time],
        [Blocked]
    FROM {tbl(company, 'Vendor')}
    WHERE [Blocked] = 0
    ORDER BY [No_]
    """
    try:
        return _to_csv(conn, sql, out / f'Vendors_{short}_{_today()}.csv')
    except Exception as e:
        print(f"    FAILED: {e}")


def pull_item_vendor(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    """Item-Vendor catalog — which vendors supply which items, at what price and lead time"""
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Item-Vendor Catalog [{company}]...")
    sql = f"""
    SELECT
        iv.[Item No_],
        iv.[Vendor No_],
        iv.[Vendor Item No_],
        iv.[Lead Time Calculation]      AS [Vendor Lead Time],
        iv.[Last Direct Cost]           AS [Last Unit Cost],
        iv.[Minimum Order Quantity]     AS [Min Order Qty],
        v.[Name]                        AS [Vendor Name],
        v.[Currency Code],
        v.[Payment Terms Code]
    FROM {tbl(company, 'Item Vendor')} iv
    LEFT JOIN {tbl(company, 'Vendor')} v ON v.[No_] = iv.[Vendor No_]
    ORDER BY iv.[Item No_], iv.[Vendor No_]
    """
    try:
        return _to_csv(conn, sql, out / f'ItemVendor_{short}_{_today()}.csv')
    except Exception as e:
        print(f"    FAILED: {e}")


def pull_return_receipts(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    """Return receipt lines — quality/return data for supplier scorecard"""
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Return Receipts [{company}]...")
    sql = f"""
    SELECT
        rl.[Document No_]               AS [Return Receipt No.],
        rh.[Buy-from Vendor No_]        AS [Vendor No.],
        rh.[Posting Date],
        rh.[Order No_]                  AS [Return Order No.],
        rl.[No_]                        AS [Item No.],
        rl.[Description],
        rl.[Quantity],
        rl.[Direct Unit Cost]           AS [Unit Cost],
        rl.[Return Reason Code]         AS [Return Reason]
    FROM {tbl(company, 'Return Receipt Line')} rl
    JOIN {tbl(company, 'Return Receipt Header')} rh
        ON rh.[No_] = rl.[Document No_]
    WHERE rh.[Posting Date] >= DATEADD(year, -2, GETDATE())
      AND rl.[Quantity] > 0
      AND rl.[Type] = 2
    ORDER BY rh.[Posting Date] DESC
    """
    try:
        return _to_csv(conn, sql, out / f'ReturnReceipts_{short}_{_today()}.csv')
    except Exception as e:
        print(f"    FAILED: {e}")


def pull_observed_lead_times(conn, company='Indelco Plastics', out=OUTPUT_DIR):
    """
    Observed Lead Time per Item × Vendor
    Table names confirmed from BC270PROD schema screenshot:
      Purch_ Rcpt_ Header, Purch_ Rcpt_ Line,
      Purchase Header Archive, Purchase Line Archive
    """
    short = COMPANIES.get(company, company.replace(' ',''))
    print(f"  Observed Lead Times [{company}]...")
    sql = f"""
    SELECT DISTINCT
        rl.[No_]                                        AS [Item No.],
        rh.[Buy-from Vendor No_]                        AS [Vendor No.],
        rh.[No_]                                        AS [Receipt No.],
        rh.[Order No_]                                  AS [PO No.],
        CONVERT(varchar(10), rh.[Posting Date], 120)    AS [Actual Receipt Date],
        CONVERT(varchar(10), ph.[Order Date], 120)      AS [Order Date],
        CONVERT(varchar(10), ph.[Expected Receipt Date], 120)  AS [Expected Receipt Date],
        CONVERT(varchar(10), ph.[Requested Receipt Date], 120) AS [Requested Receipt Date],
        CONVERT(varchar(10), pl.[Expected Receipt Date], 120) AS [Line Promised Receipt Date],
        -- Cycle time: Order Date to Receipt (for planning/ROP)
        DATEDIFF(day, ph.[Order Date], rh.[Posting Date])          AS [Actual Lead Time Days],
        -- Promised date with fallback: Expected Receipt Date → Requested Receipt Date → NULL
        CASE
            WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
            WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
            ELSE NULL
        END                                             AS [Promised Receipt Date],
        -- Promised LT: Order Date to Promised Date (for planning comparison)
        DATEDIFF(day, ph.[Order Date],
            CASE
                WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                ELSE NULL
            END)                                        AS [Promised Lead Time Days],
        -- Delivery variance: Promised Date to Actual Receipt (for scorecard)
        DATEDIFF(day,
            CASE
                WHEN ph.[Expected Receipt Date] >= ph.[Order Date] THEN ph.[Expected Receipt Date]
                WHEN ph.[Requested Receipt Date] >= ph.[Order Date] THEN ph.[Requested Receipt Date]
                ELSE NULL
            END,
            rh.[Posting Date])                          AS [Days Variance],
        -- On Time: only calculated when a valid promised date exists
        CASE
            WHEN ph.[Expected Receipt Date] >= ph.[Order Date]
              AND rh.[Posting Date] <= ph.[Expected Receipt Date] THEN 1
            WHEN ph.[Expected Receipt Date] < ph.[Order Date]
              AND ph.[Requested Receipt Date] >= ph.[Order Date]
              AND rh.[Posting Date] <= ph.[Requested Receipt Date] THEN 1
            WHEN ph.[Expected Receipt Date] >= ph.[Order Date]
              OR ph.[Requested Receipt Date] >= ph.[Order Date] THEN 0
            ELSE NULL  -- no valid promised date, exclude from OTD
        END                                             AS [On Time],
        rl.[Quantity]                                   AS [Qty Received],
        rl.[Direct Unit Cost]                           AS [Unit Cost]
    FROM {tbl(company, 'Purch_ Rcpt_ Header')} rh
    JOIN {tbl(company, 'Purch_ Rcpt_ Line')} rl
        ON  rl.[Document No_] = rh.[No_]
        AND rl.[Type] = 2
        AND rl.[Quantity] > 0
    LEFT JOIN {tbl(company, 'Purchase Header Archive')} ph
        ON  ph.[No_] = rh.[Order No_]
        AND ph.[Doc_ No_ Occurrence] = (
            SELECT MAX(ph2.[Doc_ No_ Occurrence])
            FROM {tbl(company, 'Purchase Header Archive')} ph2
            WHERE ph2.[No_] = rh.[Order No_]
        )
    LEFT JOIN {tbl(company, 'Purchase Line Archive')} pl
        ON  pl.[Document No_] = rh.[Order No_]
        AND pl.[No_] = rl.[No_]
        AND pl.[Doc_ No_ Occurrence] = ph.[Doc_ No_ Occurrence]
    WHERE rh.[Posting Date] >= DATEADD(year, -2, GETDATE())
      AND rh.[Order No_] IS NOT NULL
      AND rh.[Order No_] != ''
      AND ph.[Order Date] IS NOT NULL
      AND ph.[Document Type] = 1
      AND DATEDIFF(day, ph.[Order Date], rh.[Posting Date]) > 0
      AND DATEDIFF(day, ph.[Order Date], rh.[Posting Date]) <= 365
    """
    try:
        return _to_csv(conn, sql, out / f'ObservedLT_{short}_{_today()}.csv')
    except Exception as e:
        print(f"    FAILED: {e}")


def main():
    parser = argparse.ArgumentParser(description='Indelco SSMS Connector v4')
    parser.add_argument('--test',           action='store_true')
    parser.add_argument('--pull',           choices=['items','ile','values','po','vendor','qoh','locations','customers','vendors','itemvendor','returns','leadtimes','saleslines','all'])
    parser.add_argument('--company',        default='Indelco Plastics')
    parser.add_argument('--all-companies',  action='store_true')
    parser.add_argument('--years',          type=int, default=4)
    parser.add_argument('--output',         default=str(OUTPUT_DIR))
    args = parser.parse_args()

    out = Path(args.output)

    if args.test:
        test_connection()
        return

    if not check_prerequisites():
        sys.exit(1)

    print(f"Connecting to {SERVER} / {DATABASE}...")
    try:
        conn = get_conn()
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Make sure you are on VPN or office network.")
        sys.exit(1)
    print("Connected.\n")

    companies = list(COMPANIES.keys()) if args.all_companies else [args.company]

    for company in companies:
        print(f"-- {company} --")
        try:
            if args.pull in ('items', 'all'):
                pull_items(conn, company, out)
            if args.pull in ('ile', 'all'):
                pull_ile(conn, company, args.years, out)
            if args.pull in ('values', 'all'):
                pull_value_entries(conn, company, args.years, out)
            if args.pull in ('po', 'all'):
                pull_purchase_orders(conn, company, out)
            if args.pull in ('vendor', 'all'):
                pull_vendor_ledger(conn, company, out)
            if args.pull in ('qoh', 'all'):
                pull_qoh(conn, company, out)
            if args.pull in ('locations', 'all'):
                pull_locations(conn, company, out)
            if args.pull in ('customers', 'all'):
                pull_customers(conn, company, out)
            if args.pull in ('vendors', 'all'):
                pull_vendors(conn, company, out)
            if args.pull in ('itemvendor', 'all'):
                pull_item_vendor(conn, company, out)
            if args.pull in ('returns', 'all'):
                pull_return_receipts(conn, company, out)
            if args.pull in ('leadtimes', 'all'):
                pull_observed_lead_times(conn, company, out)
            if args.pull in ('saleslines', 'all'):
                export_sales_lines(conn, company, out)
        except Exception as e:
            print(f"  Skipped {company}: {e}")

    conn.close()
    print(f"\nDone. Files in: {out}")


def export_sales_lines(conn, company, out_dir, guid=GUID):
    """Pull Sales Shipment Lines — links SHP numbers to SO numbers for spike drilldown."""
    short = COMPANIES.get(company, company.replace(' ','_').upper()[:5])
    tbl = f"[{company}$Sales Shipment Line${guid}]"
    sql = f"""
        SELECT
            ssl.[Document No_]                              AS [Shipment No],
            ssl.[Order No_]                                 AS [SO No],
            ssl.[Line No_]                                  AS [Line No],
            ssl.[Sell-to Customer No_]                      AS [Customer No],
            ssl.[No_]                                       AS [Item No],
            ssl.[Description]                               AS [Description],
            ssl.[Location Code]                             AS [Location Code],
            ssl.[Quantity]                                  AS [Quantity],
            ssl.[Unit Price]                                AS [Unit Price],
            ssl.[Line Discount _]                           AS [Line Discount Pct],
            ssl.[Quantity] * ssl.[Unit Price] * (1.0 - ssl.[Line Discount _] / 100.0) AS [Amount],
            CONVERT(varchar(10), ssl.[Posting Date], 120)   AS [Shipment Date],
            ssl.[Item Category Code]                        AS [Item Category Code],
            ssl.[Drop Shipment]                             AS [Drop Shipment]
        FROM {tbl} ssl
        WHERE ssl.[No_] IS NOT NULL AND ssl.[No_] != ''
          AND ssl.[Type] = 2
          AND ssl.[Posting Date] >= DATEADD(month, -12, GETDATE())
    """
    try:
        return _to_csv(conn, sql, Path(out_dir) / f'SalesLines_{short}_{_today()}.csv')
    except Exception as e:
        print(f" SKIPPED: {e}")


if __name__ == '__main__':
    main()
