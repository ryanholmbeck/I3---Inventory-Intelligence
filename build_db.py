"""
Indelco DB Builder v1
Reads all CSVs from SSMS_Exports and builds indelco.db
Runs in Python — handles millions of rows in seconds

Usage:
  python build_db.py --mode historical   # AYER + CORR + QS (run once)
  python build_db.py --mode live         # INDELCO only (run daily)
  python build_db.py --mode full         # all companies (first-ever run)
"""

import sqlite3, csv, os, sys, argparse, glob, time
from pathlib import Path
from datetime import datetime, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
EXPORTS_DIR = BASE_DIR / 'SSMS_Exports'
DB_LIVE     = BASE_DIR / 'indelco_live.db'
DB_HIST     = BASE_DIR / 'indelco_historical.db'
DB_COMBINED = BASE_DIR / 'indelco.db'   # what the HTML app loads

CHUNK = 50000   # rows per transaction — much larger than browser (Python is fast)

# Entry type integer → text (matches what connector now outputs as text anyway)
ET_MAP = {
    '0':'Purchase','1':'Sale','2':'Positive Adjmt.','3':'Negative Adjmt.',
    '4':'Transfer','5':'Consumption','6':'Output',
    '7':'Assembly Consumption','8':'Assembly Output'
}

def et(v):
    v = str(v or '').strip()
    return ET_MAP.get(v, v)

def pf(v):
    try: return float(str(v or '0').replace(',',''))
    except: return 0.0

def pb(v):
    return 1 if str(v or '').strip().upper() in ('TRUE','YES','1') else 0

def pdate(v):
    if not v: return None
    v = str(v).strip()
    if not v or v == 'nan': return None
    for fmt in ('%Y-%m-%d','%m/%d/%Y','%d/%m/%Y'):
        try: return datetime.strptime(v[:10], fmt).strftime('%Y-%m-%d')
        except: pass
    try:
        n = float(v)
        if 30000 < n < 60000:
            return (datetime(1899,12,30)+timedelta(days=n)).strftime('%Y-%m-%d')
    except: pass
    return v[:10] if len(v) >= 10 else v

def nk(k, aliases):
    return aliases.get(k.lower().strip(), k.lower().strip())

# ── Column alias maps ─────────────────────────────────────────────────
ITEM_ALIASES = {
    'no.':'item_no','no':'item_no','item no.':'item_no','item no':'item_no',
    'description':'description','description 2':'description_2',
    'base unit of measure':'base_uom','unit cost':'unit_cost',
    'standard cost':'standard_cost','unit price':'unit_price',
    'vendor no.':'vendor_no','vendor no':'vendor_no',
    'blocked':'blocked','lead time calculation':'lead_time_calc',
    'item category code':'item_category_code',
    'last date modified':'last_date_modified',
    'quantity on hand':'qty_on_hand','qty. on hand':'qty_on_hand',
    'inventory posting group':'inventory_posting_group',
    'reordering policy':'reordering_policy','reorder point':'reorder_point',
    'safety stock quantity':'safety_stock_qty',
    'minimum order quantity':'minimum_order_qty','min. order qty':'minimum_order_qty',
    'maximum order quantity':'maximum_order_qty','max. order qty':'maximum_order_qty',
    'reorder quantity':'reorder_quantity','maximum inventory':'maximum_inventory',
    'costing method':'costing_method','purchasing code':'purchasing_code',
    'sales blocked':'sales_blocked','purchasing blocked':'purchasing_blocked',
    'inventory planning group':'inventory_planning_group',
    'drop shipment':'drop_shipment_flag',
}

ILE_ALIASES = {
    'posting date':'posting_date','entry type':'entry_type',
    'document type':'document_type',
    'document no.':'document_no','document no':'document_no',
    'item no.':'item_no','item no':'item_no',
    'description':'description','location code':'location_code',
    'quantity':'quantity','invoiced quantity':'invoiced_quantity',
    'remaining quantity':'remaining_quantity',
    'source no.':'source_no','source no':'source_no',
    'source type':'source_type',
    'entry no.':'entry_no','entry no':'entry_no',
    'item category code':'item_category_code','drop shipment':'drop_shipment',
    'order type':'order_type','order no.':'order_no','order no':'order_no',
    'branch code':'branch_code','global dimension 1 code':'branch_code',
    'company source':'company_source',
}

VE_ALIASES = {
    'posting date':'posting_date',
    'entry no.':'entry_no','entry no_':'entry_no',
    'item no.':'item_no','item no':'item_no',
    'item ledger entry no.':'ile_entry_no',
    'item ledger entry type':'ile_entry_type',
    'document no.':'document_no','document type':'document_type',
    'description':'description','location code':'location_code',
    'source no.':'source_no','source type':'source_type',
    'invoiced quantity':'invoiced_quantity','valued quantity':'valued_quantity',
    'cost amount (actual)':'cost_amount_actual',
    'cost amount (expected)':'cost_amount_expected',
    'sales amount (actual)':'sales_amount_actual',
    'sales amount (expected)':'sales_amount_expected',
    'purchase amount (actual)':'purchase_amount_actual',
    'cost per unit':'cost_per_unit',
    'cost posted to g_l':'cost_posted_to_gl',
    'drop shipment':'drop_shipment','expected cost':'expected_cost',
    'item charge no.':'item_charge_no',
    'global dimension 1 code':'branch_code','branch code':'branch_code',
    'order type':'order_type','order no.':'order_no',
    'company source':'company_source',
}

PO_ALIASES = {
    'po no.':'po_no','po no':'po_no',
    'vendor no.':'vendor_no','vendor no':'vendor_no',
    'vendor name':'vendor_name','order date':'order_date',
    'expected receipt date':'expected_receipt','status':'status',
    'location code':'location_code','completely received':'completely_received',
    'line no.':'line_no','line no':'line_no',
    'item no.':'item_no','item no':'item_no',
    'description':'description','quantity':'quantity',
    'quantity received':'qty_received','outstanding quantity':'outstanding_qty',
    'direct unit cost':'unit_cost','unit cost':'unit_cost',
    'drop shipment':'drop_shipment','purchaser code':'purchaser_code',
    'is late':'is_late','days late':'days_late',
    'unconfirmed':'unconfirmed','on time':'on_time',
    'qty rni':'qty_rni','rni value':'rni_value',
}

# ── Schema ────────────────────────────────────────────────────────────
def build_schema(conn):
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;
    PRAGMA cache_size=-64000;
    PRAGMA temp_store=MEMORY;

    CREATE TABLE IF NOT EXISTS parameters (
        key TEXT PRIMARY KEY, value TEXT, units TEXT, notes TEXT);

    CREATE TABLE IF NOT EXISTS locations (
        location_code TEXT PRIMARY KEY, location_name TEXT,
        company_prefix TEXT, is_active INTEGER DEFAULT 1,
        is_legacy INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS items (
        item_no TEXT PRIMARY KEY, description TEXT, description_2 TEXT,
        base_uom TEXT, unit_cost REAL DEFAULT 0, standard_cost REAL DEFAULT 0,
        unit_price REAL DEFAULT 0, vendor_no TEXT, blocked INTEGER DEFAULT 0,
        lead_time_calc TEXT, item_category_code TEXT, last_date_modified TEXT,
        qty_on_hand REAL DEFAULT 0, qty_on_purch_order REAL DEFAULT 0,
        qty_on_sales_order REAL DEFAULT 0, inventory_posting_group TEXT,
        reordering_policy TEXT, reorder_point REAL DEFAULT 0,
        safety_stock_qty REAL DEFAULT 0, minimum_order_qty REAL DEFAULT 0,
        maximum_order_qty REAL DEFAULT 0, reorder_quantity REAL DEFAULT 0,
        maximum_inventory REAL DEFAULT 0, costing_method TEXT,
        purchasing_code TEXT, sales_blocked INTEGER DEFAULT 0,
        purchasing_blocked INTEGER DEFAULT 0,
        inventory_planning_group TEXT, drop_shipment_flag INTEGER DEFAULT 0,
        item_tracking_code TEXT);

    CREATE TABLE IF NOT EXISTS ile_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        posting_date TEXT, entry_type TEXT, document_no TEXT,
        item_no TEXT NOT NULL, description TEXT, location_code TEXT,
        quantity REAL DEFAULT 0, invoiced_quantity REAL DEFAULT 0,
        remaining_quantity REAL DEFAULT 0, source_no TEXT, source_type TEXT,
        entry_no TEXT, item_category_code TEXT, drop_shipment INTEGER DEFAULT 0,
        order_type TEXT, order_no TEXT, branch_code TEXT,
        company_source TEXT, batch_id TEXT);

    CREATE INDEX IF NOT EXISTS idx_ile_item ON ile_transactions(item_no);
    CREATE INDEX IF NOT EXISTS idx_ile_loc  ON ile_transactions(location_code);
    CREATE INDEX IF NOT EXISTS idx_ile_date ON ile_transactions(posting_date);
    CREATE INDEX IF NOT EXISTS idx_ile_type ON ile_transactions(entry_type);

    CREATE TABLE IF NOT EXISTS value_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        posting_date TEXT, entry_no TEXT, item_no TEXT,
        ile_entry_no TEXT, ile_entry_type TEXT, document_no TEXT,
        document_type TEXT, description TEXT, location_code TEXT,
        source_no TEXT, source_type TEXT,
        invoiced_quantity REAL DEFAULT 0, valued_quantity REAL DEFAULT 0,
        cost_amount_actual REAL DEFAULT 0, cost_amount_expected REAL DEFAULT 0,
        sales_amount_actual REAL DEFAULT 0, sales_amount_expected REAL DEFAULT 0,
        purchase_amount_actual REAL DEFAULT 0, cost_per_unit REAL DEFAULT 0,
        cost_posted_to_gl REAL DEFAULT 0, drop_shipment INTEGER DEFAULT 0,
        expected_cost INTEGER DEFAULT 0, item_charge_no TEXT,
        branch_code TEXT, order_type TEXT, order_no TEXT,
        company_source TEXT, batch_id TEXT);

    CREATE INDEX IF NOT EXISTS idx_ve_item ON value_entries(item_no);
    CREATE INDEX IF NOT EXISTS idx_ve_date ON value_entries(posting_date);

    DROP TABLE IF EXISTS purchase_orders;
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_no TEXT, vendor_no TEXT, vendor_name TEXT,
        order_date TEXT, expected_receipt TEXT, status TEXT,
        location_code TEXT, completely_received INTEGER DEFAULT 0,
        line_no TEXT, item_no TEXT, description TEXT,
        quantity REAL DEFAULT 0, qty_received REAL DEFAULT 0,
        outstanding_qty REAL DEFAULT 0, unit_cost REAL DEFAULT 0,
        is_late INTEGER DEFAULT 0, days_late INTEGER DEFAULT 0,
        unconfirmed INTEGER DEFAULT 0, on_time INTEGER DEFAULT 0,
        qty_rni REAL DEFAULT 0, rni_value REAL DEFAULT 0,
        drop_shipment INTEGER DEFAULT 0, purchaser_code TEXT);

    CREATE TABLE IF NOT EXISTS item_qoh (
        item_no TEXT, location_code TEXT, qty_on_hand REAL DEFAULT 0,
        PRIMARY KEY(item_no, location_code));

    CREATE TABLE IF NOT EXISTS ile_summary (
        item_no TEXT, location_code TEXT,
        adu REAL DEFAULT 0, std_dev REAL DEFAULT 0,
        adu_sales REAL DEFAULT 0, adu_mfg REAL DEFAULT 0,
        tx_count INTEGER DEFAULT 0, outliers_excluded INTEGER DEFAULT 0,
        tx_count_sales INTEGER DEFAULT 0, tx_count_mfg INTEGER DEFAULT 0,
        total_qty_used REAL DEFAULT 0, date_last TEXT, last_aggregated TEXT,
        PRIMARY KEY(item_no, location_code));

    CREATE TABLE IF NOT EXISTS item_calculator (
        item_no TEXT, location_code TEXT, abc_class TEXT,
        annualized_spend REAL DEFAULT 0, abc_rank INTEGER,
        lead_time_days INTEGER DEFAULT 5, std_box_qty REAL DEFAULT 1,
        order_freq_days INTEGER DEFAULT 10,
        adu REAL DEFAULT 0, std_dev REAL DEFAULT 0,
        adu_sales REAL DEFAULT 0, adu_mfg REAL DEFAULT 0,
        ss_days INTEGER DEFAULT 10, safety_stock REAL DEFAULT 0,
        bin1_trigger REAL DEFAULT 0, bin2_replenish REAL DEFAULT 0,
        system_type TEXT, reorder_point REAL DEFAULT 0,
        min_order_qty REAL DEFAULT 0, max_order_qty REAL DEFAULT 0,
        stocking_recommendation TEXT, stocking_flags TEXT,
        excluded INTEGER DEFAULT 0, exclude_reason TEXT,
        days_since_last_movement INTEGER, aging_flag INTEGER DEFAULT 0,
        overstock_flag INTEGER DEFAULT 0, overstock_ratio REAL DEFAULT 0,
        date_last_movement TEXT, calculated_at TEXT,
        PRIMARY KEY(item_no, location_code));

    CREATE TABLE IF NOT EXISTS item_master_supplement (
        item_no TEXT PRIMARY KEY, returnable INTEGER,
        return_window_days INTEGER, nncr_flag INTEGER DEFAULT 0,
        customer_contract_flag INTEGER DEFAULT 0,
        drop_ship_only INTEGER DEFAULT 0,
        physical_space_available INTEGER DEFAULT 1,
        customer_count INTEGER, stocking_override TEXT, notes TEXT);

    CREATE TABLE IF NOT EXISTS std_box_qty (
        item_no TEXT PRIMARY KEY, std_box_qty REAL DEFAULT 1);

    CREATE TABLE IF NOT EXISTS exclusions (
        item_no TEXT, location_code TEXT,
        excluded INTEGER DEFAULT 1, reason TEXT,
        PRIMARY KEY(item_no, location_code));

    CREATE TABLE IF NOT EXISTS import_batches (
        batch_id TEXT PRIMARY KEY, import_type TEXT,
        row_count INTEGER, rows_inserted INTEGER,
        rows_rejected INTEGER, imported_at TEXT);

    CREATE TABLE IF NOT EXISTS db_meta (
        key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE IF NOT EXISTS customers (
        customer_no TEXT PRIMARY KEY, name TEXT, city TEXT,
        state TEXT, country TEXT, payment_terms TEXT,
        credit_limit REAL DEFAULT 0, blocked INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS vendors (
        vendor_no TEXT PRIMARY KEY, name TEXT, city TEXT,
        state TEXT, country TEXT, currency_code TEXT,
        payment_terms TEXT, lead_time TEXT, blocked INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS item_vendor (
        item_no TEXT, vendor_no TEXT, vendor_item_no TEXT,
        vendor_lead_time TEXT, last_unit_cost REAL DEFAULT 0,
        min_order_qty REAL DEFAULT 0,
        PRIMARY KEY (item_no, vendor_no));

    CREATE TABLE IF NOT EXISTS return_shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_shipment_no TEXT, vendor_no TEXT, posting_date TEXT,
        return_order_no TEXT, item_no TEXT, description TEXT,
        quantity REAL DEFAULT 0, unit_cost REAL DEFAULT 0,
        return_reason TEXT);

    DROP TABLE IF EXISTS sales_lines;
    CREATE TABLE IF NOT EXISTS sales_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shipment_no TEXT, so_no TEXT, line_no TEXT, customer_no TEXT,
        item_no TEXT, description TEXT, location_code TEXT,
        quantity REAL DEFAULT 0, unit_price REAL DEFAULT 0,
        line_discount_pct REAL DEFAULT 0, amount REAL DEFAULT 0,
        shipment_date TEXT, item_category_code TEXT,
        drop_shipment INTEGER DEFAULT 0, company TEXT);
    CREATE INDEX IF NOT EXISTS idx_sl_shp ON sales_lines(shipment_no);
    CREATE INDEX IF NOT EXISTS idx_sl_so ON sales_lines(so_no);
    CREATE INDEX IF NOT EXISTS idx_sl_item ON sales_lines(item_no);

    CREATE TABLE IF NOT EXISTS observed_lead_times (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_no TEXT, vendor_no TEXT, receipt_no TEXT, po_no TEXT,
        order_date TEXT, promised_receipt_date TEXT, actual_receipt_date TEXT,
        actual_lt_days INTEGER, promised_lt_days INTEGER,
        days_variance INTEGER, on_time INTEGER,
        qty_received REAL DEFAULT 0, unit_cost REAL DEFAULT 0);

    CREATE INDEX IF NOT EXISTS idx_olt_item ON observed_lead_times(item_no);
    CREATE INDEX IF NOT EXISTS idx_olt_vendor ON observed_lead_times(vendor_no);

    CREATE TABLE IF NOT EXISTS xyz_classification (
        item_no TEXT, location_code TEXT, xyz_class TEXT, cov REAL,
        classified_at TEXT, PRIMARY KEY(item_no, location_code));

    CREATE TABLE IF NOT EXISTS spike_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_no TEXT, location_code TEXT, posting_date TEXT,
        entry_type TEXT, document_no TEXT, order_no TEXT,
        source_no TEXT, customer_name TEXT,
        quantity REAL, item_mean REAL, item_std_dev REAL,
        z_score REAL, lookback_window INTEGER,
        logged_at TEXT);

    CREATE TABLE IF NOT EXISTS service_level_params (
        abc_class TEXT, xyz_class TEXT,
        target_service_level REAL DEFAULT 0.95,
        ss_method TEXT DEFAULT 'days',
        PRIMARY KEY(abc_class, xyz_class));

    CREATE TABLE IF NOT EXISTS supplier_scorecard (
        vendor_no TEXT, location_code TEXT, period_start TEXT,
        total_orders INTEGER DEFAULT 0, on_time_orders INTEGER DEFAULT 0,
        otd_pct REAL DEFAULT 0, avg_days_variance REAL DEFAULT 0,
        total_lines INTEGER DEFAULT 0, fill_rate_pct REAL DEFAULT 0,
        return_qty REAL DEFAULT 0, return_pct REAL DEFAULT 0,
        calculated_at TEXT, PRIMARY KEY(vendor_no, location_code, period_start));
    """)

    # Default parameters
    defaults = [
        ('abc_a_threshold','0.80','%','Top 80% annualized spend = A items'),
        ('abc_b_threshold','0.95','%','80-95% = B items'),
        ('order_freq_a','10','Days','Replenishment cycle A items'),
        ('order_freq_b','20','Days','Replenishment cycle B items'),
        ('order_freq_c','30','Days','Replenishment cycle C items'),
        ('ss_days_a','10','Days','Safety stock days A items'),
        ('ss_days_b','15','Days','Safety stock days B items'),
        ('ss_days_c','20','Days','Safety stock days C items'),
        ('working_days_year','252','Days','Business calendar days'),
        ('outlier_std_dev','2','xStdDev','Exclude > N std devs from mean'),
        ('min_order_qty_floor','1','Units','Minimum allowed order quantity'),
        ('one_bin_adu_threshold','5','Units/Day','ADU <= this = 1-bin system'),
        ('default_reorder_policy','Fixed Reorder Qty','','BC Reordering Policy'),
        ('default_lot_accum_period','10D','','BC Lot Accumulation Period'),
        ('default_resch_period','20D','','BC Rescheduling Period'),
        ('ile_lookback_days','182','Days','Days of ILE history for ADU'),
        ('aging_no_movement_days','180','Days','Days idle before aging flag'),
        ('aging_overstock_multiplier','2.0','x','OH > N x annual = overstock'),
    ]
    conn.executemany(
        'INSERT OR IGNORE INTO parameters VALUES (?,?,?,?)',
        [(k,v,u,n) for k,v,u,n in defaults])
    conn.commit()

# ── CSV reader ────────────────────────────────────────────────────────
def read_csv(filepath):
    """Read CSV with UTF-8-sig encoding (handles BOM from SSMS exports)"""
    with open(filepath, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        return list(reader)

def latest_csv(pattern):
    """Find the most recent CSV matching a glob pattern"""
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None

def find_csv(pattern, dirs):
    """Find newest CSV matching pattern, searching dirs in order.

    Returns (path, source_dir_name) or (None, None). The order of `dirs`
    sets the priority — first directory with a matching file wins.
    This is how hybrid SSMS+BC mode works: BC_Exports first, fall back
    to SSMS_Exports for tables BC hasn't published yet.
    """
    for d in dirs:
        f = latest_csv(str(Path(d) / pattern))
        if f:
            return f, Path(d).name
    return None, None

# ── Importers ─────────────────────────────────────────────────────────
def import_items(conn, filepath, company_source='INDELCO'):
    print(f'  Items: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    inserted = rejected = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        conn.execute('BEGIN')
        for raw in chunk:
            row = {nk(k, ITEM_ALIASES): v for k,v in raw.items()}
            no = str(row.get('item_no','')).strip()
            if not no: rejected += 1; continue
            try:
                conn.execute("""
                    INSERT INTO items(item_no,description,description_2,base_uom,
                        unit_cost,standard_cost,unit_price,vendor_no,blocked,
                        lead_time_calc,item_category_code,last_date_modified,
                        qty_on_hand,inventory_posting_group,reordering_policy,
                        reorder_point,safety_stock_qty,minimum_order_qty,
                        maximum_order_qty,reorder_quantity,maximum_inventory,
                        costing_method,purchasing_code,sales_blocked,
                        purchasing_blocked,inventory_planning_group,drop_shipment_flag)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(item_no) DO UPDATE SET
                        description=excluded.description,
                        unit_cost=excluded.unit_cost,
                        qty_on_hand=excluded.qty_on_hand,
                        lead_time_calc=excluded.lead_time_calc,
                        item_category_code=excluded.item_category_code,
                        inventory_posting_group=excluded.inventory_posting_group,
                        inventory_planning_group=excluded.inventory_planning_group,
                        safety_stock_qty=excluded.safety_stock_qty,
                        reorder_point=excluded.reorder_point
                """, (no, row.get('description',''), row.get('description_2',''),
                    row.get('base_uom','EA'), pf(row.get('unit_cost')),
                    pf(row.get('standard_cost')), pf(row.get('unit_price')),
                    row.get('vendor_no',''), pb(row.get('blocked')),
                    row.get('lead_time_calc',''), row.get('item_category_code',''),
                    pdate(row.get('last_date_modified')),
                    pf(row.get('qty_on_hand')),
                    row.get('inventory_posting_group',''),
                    row.get('reordering_policy',''),
                    pf(row.get('reorder_point')), pf(row.get('safety_stock_qty')),
                    pf(row.get('minimum_order_qty')), pf(row.get('maximum_order_qty')),
                    pf(row.get('reorder_quantity')), pf(row.get('maximum_inventory')),
                    row.get('costing_method',''), row.get('purchasing_code',''),
                    pb(row.get('sales_blocked')), pb(row.get('purchasing_blocked')),
                    row.get('inventory_planning_group',''),
                    pb(row.get('drop_shipment_flag'))))
                inserted += 1
            except Exception as e:
                rejected += 1
        conn.execute('COMMIT')
    print(f' → {inserted:,} rows ({rejected} rejected)')
    return inserted

def import_ile(conn, filepath, company_source='INDELCO'):
    print(f'  ILE: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    bid = Path(filepath).stem[-8:]
    inserted = rejected = 0
    locs = set()
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        conn.execute('BEGIN')
        for raw in chunk:
            row = {nk(k, ILE_ALIASES): v for k,v in raw.items()}
            no = str(row.get('item_no','')).strip()
            pd = pdate(row.get('posting_date'))
            if not no or not pd: rejected += 1; continue
            loc = str(row.get('location_code','')).strip()
            locs.add(loc)
            entry_type = et(row.get('entry_type',''))
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO ile_transactions(
                        posting_date,entry_type,document_no,item_no,description,
                        location_code,quantity,invoiced_quantity,remaining_quantity,
                        source_no,source_type,entry_no,item_category_code,
                        drop_shipment,order_type,order_no,branch_code,
                        company_source,batch_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (pd, entry_type, row.get('document_no',''), no,
                    row.get('description',''), loc, pf(row.get('quantity')),
                    pf(row.get('invoiced_quantity')),
                    pf(row.get('remaining_quantity')),
                    row.get('source_no',''), row.get('source_type',''),
                    row.get('entry_no',''), row.get('item_category_code',''),
                    pb(row.get('drop_shipment')),
                    row.get('order_type',''), row.get('order_no',''),
                    row.get('branch_code',''),
                    row.get('company_source', company_source), bid))
                inserted += 1
            except: rejected += 1
        conn.execute('COMMIT')
        pct = min(100, int((i+len(chunk))/len(rows)*100))
        print(f'\r  ILE: {Path(filepath).name} → {i+len(chunk):,}/{len(rows):,} ({pct}%)  ', end='', flush=True)
    for lc in locs:
        if lc: conn.execute('INSERT OR IGNORE INTO locations(location_code,is_active) VALUES(?,1)', (lc,))
    conn.commit()
    print(f'\r  ILE: {Path(filepath).name} → {inserted:,} rows ({rejected} rejected)          ')
    return inserted

def import_value_entries(conn, filepath, company_source='INDELCO'):
    print(f'  Value Entries: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    bid = Path(filepath).stem[-8:]
    inserted = rejected = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        conn.execute('BEGIN')
        for raw in chunk:
            row = {nk(k, VE_ALIASES): v for k,v in raw.items()}
            no = str(row.get('item_no','')).strip()
            pd = pdate(row.get('posting_date'))
            if not no or not pd: rejected += 1; continue
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO value_entries(
                        posting_date,entry_no,item_no,ile_entry_no,ile_entry_type,
                        document_no,document_type,description,location_code,
                        source_no,source_type,invoiced_quantity,valued_quantity,
                        cost_amount_actual,cost_amount_expected,sales_amount_actual,
                        sales_amount_expected,purchase_amount_actual,cost_per_unit,
                        cost_posted_to_gl,drop_shipment,expected_cost,item_charge_no,
                        branch_code,order_type,order_no,company_source,batch_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (pd, row.get('entry_no',''), no, row.get('ile_entry_no',''),
                    row.get('ile_entry_type',''), row.get('document_no',''),
                    row.get('document_type',''), row.get('description',''),
                    str(row.get('location_code','')).strip(),
                    row.get('source_no',''), row.get('source_type',''),
                    pf(row.get('invoiced_quantity')), pf(row.get('valued_quantity')),
                    pf(row.get('cost_amount_actual')), pf(row.get('cost_amount_expected')),
                    pf(row.get('sales_amount_actual')), pf(row.get('sales_amount_expected')),
                    pf(row.get('purchase_amount_actual')), pf(row.get('cost_per_unit')),
                    pf(row.get('cost_posted_to_gl')), pb(row.get('drop_shipment')),
                    pb(row.get('expected_cost')), row.get('item_charge_no',''),
                    row.get('branch_code',''), row.get('order_type',''),
                    row.get('order_no',''),
                    row.get('company_source', company_source), bid))
                inserted += 1
            except: rejected += 1
        conn.execute('COMMIT')
    print(f' → {inserted:,} rows ({rejected} rejected)')
    return inserted

def import_po(conn, filepath):
    print(f'  PO: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    inserted = rejected = 0
    conn.execute('BEGIN')
    for raw in rows:
        row = {nk(k, PO_ALIASES): v for k,v in raw.items()}
        if not row.get('po_no'): rejected += 1; continue
        try:
            conn.execute("""
                INSERT INTO purchase_orders(
                    po_no,vendor_no,vendor_name,order_date,expected_receipt,
                    status,location_code,completely_received,line_no,item_no,
                    description,quantity,qty_received,outstanding_qty,unit_cost,
                    is_late,days_late,unconfirmed,on_time,qty_rni,rni_value,
                    drop_shipment,purchaser_code)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (row.get('po_no',''), row.get('vendor_no',''),
                row.get('vendor_name',''), pdate(row.get('order_date')),
                pdate(row.get('expected_receipt')), row.get('status',''),
                row.get('location_code',''), pb(row.get('completely_received')),
                row.get('line_no',''), row.get('item_no',''),
                row.get('description',''), pf(row.get('quantity')),
                pf(row.get('qty_received')), pf(row.get('outstanding_qty')),
                pf(row.get('unit_cost')), pb(row.get('is_late')),
                pf(row.get('days_late')), pb(row.get('unconfirmed')),
                pb(row.get('on_time')), pf(row.get('qty_rni')),
                pf(row.get('rni_value')),
                pb(row.get('drop_shipment')),
                row.get('purchaser_code','') or None))
            inserted += 1
        except: rejected += 1
    conn.commit()
    print(f' → {inserted:,} rows ({rejected} rejected)')
    return inserted

def import_qoh(conn, filepath):
    print(f'  QoH: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    inserted = 0
    conn.execute('BEGIN')
    for raw in rows:
        row = {k.lower().strip(): v for k,v in raw.items()}
        no = str(row.get('item no.', row.get('item no', row.get('item_no','')))).strip()
        loc = str(row.get('location code', row.get('location_code',''))).strip()
        qty = pf(row.get('qty on hand', row.get('qty_on_hand', 0)))
        if not no: continue
        conn.execute('INSERT OR REPLACE INTO item_qoh(item_no,location_code,qty_on_hand) VALUES(?,?,?)', (no,loc,qty))
        conn.execute('UPDATE items SET qty_on_hand=? WHERE item_no=?', (qty, no))
        inserted += 1
    conn.commit()
    print(f' → {inserted:,} records')

def import_locations(conn, filepath):
    print(f'  Locations: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    inserted = 0
    conn.execute('BEGIN')
    for raw in rows:
        row = {k.lower().strip(): v for k,v in raw.items()}
        code = str(row.get('code','')).strip()
        name = str(row.get('name','')).strip()
        if not code: continue
        conn.execute('INSERT OR REPLACE INTO locations(location_code,location_name,is_active) VALUES(?,?,1)', (code,name))
        inserted += 1
    conn.commit()
    print(f' → {inserted} locations')

def set_meta(conn, key, value):
    conn.execute('INSERT OR REPLACE INTO db_meta VALUES(?,?)', (key, str(value)))
    conn.commit()

# ── Main build functions ───────────────────────────────────────────────

def import_customers(conn, filepath):
    """Import customer master CSV"""
    import csv
    inserted = 0
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            no = row.get('Customer No.','').strip()
            if not no: continue
            conn.execute("""
                INSERT OR REPLACE INTO customers
                  (customer_no, name, city, state, country, payment_terms, credit_limit, blocked)
                VALUES (?,?,?,?,?,?,?,?)""",
                (no, row.get('Name',''), row.get('City',''), row.get('State',''),
                 row.get('Country',''), row.get('Payment Terms Code',''),
                 pf(row.get('Credit Limit',0)), pb(row.get('Blocked',0))))
            inserted += 1
    conn.commit()
    return inserted


def import_vendors(conn, filepath):
    """Import vendor master CSV"""
    import csv
    inserted = 0
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            no = row.get('Vendor No.','').strip()
            if not no: continue
            conn.execute("""
                INSERT OR REPLACE INTO vendors
                  (vendor_no, name, city, state, country, currency_code,
                   payment_terms, lead_time, blocked)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (no, row.get('Name',''), row.get('City',''), row.get('State',''),
                 row.get('Country',''), row.get('Currency Code',''),
                 row.get('Payment Terms Code',''), row.get('Lead Time',''),
                 pb(row.get('Blocked',0))))
            inserted += 1
    conn.commit()
    return inserted


def import_item_vendor(conn, filepath):
    """Import item-vendor catalog CSV"""
    import csv
    inserted = 0
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            item = row.get('Item No.','').strip()
            vendor = row.get('Vendor No.','').strip()
            if not item or not vendor: continue
            conn.execute("""
                INSERT OR REPLACE INTO item_vendor
                  (item_no, vendor_no, vendor_item_no, vendor_lead_time,
                   last_unit_cost, min_order_qty)
                VALUES (?,?,?,?,?,?)""",
                (item, vendor, row.get('Vendor Item No.',''),
                 row.get('Vendor Lead Time',''),
                 pf(row.get('Last Unit Cost',0)), 0))
            inserted += 1
    conn.commit()
    return inserted


def import_return_shipments(conn, filepath):
    """Import return shipments (vendor returns) CSV"""
    import csv
    inserted = 0
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            doc = row.get('Return Shipment No.','').strip()
            if not doc: continue
            conn.execute("""
                INSERT OR IGNORE INTO return_shipments
                  (return_shipment_no, vendor_no, posting_date, return_order_no,
                   item_no, description, quantity, unit_cost, return_reason)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (doc, row.get('Vendor No.',''),
                 row.get('Posting Date',''), row.get('Return Order No.',''),
                 row.get('Item No.',''), row.get('Description',''),
                 pf(row.get('Quantity',0)), pf(row.get('Unit Cost',0)),
                 row.get('Return Reason','')))
            inserted += 1
    conn.commit()
    return inserted


SALES_LINES_ALIASES = {
    'shipment no':'shipment_no', 'shipment no.':'shipment_no',
    'so no':'so_no', 'so no.':'so_no',
    'line no':'line_no', 'line no.':'line_no',
    'customer no':'customer_no', 'customer no.':'customer_no',
    'item no':'item_no', 'item no.':'item_no',
    'description':'description', 'location code':'location_code',
    'quantity':'quantity', 'unit price':'unit_price',
    'line discount pct':'line_discount_pct', 'amount':'amount',
    'shipment date':'shipment_date',
    'item category code':'item_category_code',
    'drop shipment':'drop_shipment',
}

def import_sales_lines(conn, filepath, company=''):
    from pathlib import Path
    print(f'  SalesLines: {Path(filepath).name}', end='', flush=True)
    rows = read_csv(filepath)
    inserted = rejected = 0
    conn.execute('BEGIN')
    for raw in rows:
        row = {nk(k, SALES_LINES_ALIASES): v for k,v in raw.items()}
        if not row.get('item_no') or not row.get('shipment_no'):
            rejected += 1; continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO sales_lines(
                    shipment_no, so_no, line_no, customer_no, item_no, description,
                    location_code, quantity, unit_price, line_discount_pct,
                    amount, shipment_date, item_category_code,
                    drop_shipment, company)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get('shipment_no',''), row.get('so_no',''),
                 row.get('line_no',''), row.get('customer_no',''),
                 row.get('item_no',''), row.get('description',''),
                 row.get('location_code',''),
                 pf(row.get('quantity')), pf(row.get('unit_price')),
                 pf(row.get('line_discount_pct')), pf(row.get('amount')),
                 pdate(row.get('shipment_date')),
                 row.get('item_category_code',''),
                 pb(row.get('drop_shipment')), company))
            inserted += 1
        except Exception as e:
            rejected += 1
            if rejected == 1: print(f'\n    First reject: {e}', end='')
    conn.commit()
    print(f' -> {inserted:,} rows ({rejected} rejected)')
    return inserted

def import_observed_lt(conn, filepath):
    """Import observed lead times CSV"""
    import csv
    inserted = 0
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            item = row.get('Item No.','').strip()
            vendor = row.get('Vendor No.','').strip()
            if not item or not vendor: continue
            actual_lt = pf(row.get('Actual Lead Time Days',0))
            if not actual_lt or actual_lt <= 0: continue
            conn.execute("""
                INSERT OR IGNORE INTO observed_lead_times
                  (item_no, vendor_no, receipt_no, po_no,
                   order_date, promised_receipt_date, actual_receipt_date,
                   actual_lt_days, promised_lt_days, days_variance,
                   on_time, qty_received, unit_cost)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item, vendor, row.get('Receipt No.',''), row.get('PO No.',''),
                 row.get('Order Date',''), row.get('Promised Receipt Date',''),
                 row.get('Actual Receipt Date',''),
                 int(actual_lt), pf(row.get('Promised Lead Time Days',0)),
                 pf(row.get('Days Variance',0)), pb(row.get('On Time',0)),
                 pf(row.get('Qty Received',0)), pf(row.get('Unit Cost',0))))
            inserted += 1
    conn.commit()
    return inserted


def build_historical(exports_dir, fallback_dir=None):
    """Build indelco_historical.db from AYER, CORR, QS files — run once."""
    print(f'\n{"="*55}')
    print('  Building historical database (AYER + CORR + QS)')
    print(f'{"="*55}')
    t0 = time.time()

    if DB_HIST.exists():
        DB_HIST.unlink()
        print(f'  Removed old {DB_HIST.name}')

    conn = sqlite3.connect(DB_HIST)
    build_schema(conn)

    search_dirs = [exports_dir] + ([Path(fallback_dir)] if fallback_dir else [])

    for company in ['AYER', 'CORR', 'QS']:
        print(f'\n  ── {company} ──')
        for pattern, fn in [
            (f'Items_{company}_*.csv', lambda f: import_items(conn, f, company)),
            (f'ILE_{company}_*.csv',   lambda f: import_ile(conn, f, company)),
            (f'ValueEntry_{company}_*.csv', lambda f: import_value_entries(conn, f, company)),
        ]:
            f, _ = find_csv(pattern, search_dirs)
            if f: fn(f)
            else: print(f'  (no {pattern} found — skipping)')

    # Ensure all location codes from ILE are in locations table
    conn.execute("""
        INSERT OR IGNORE INTO locations(location_code, is_active)
        SELECT DISTINCT location_code, 1
        FROM ile_transactions
        WHERE location_code IS NOT NULL AND location_code != ''
    """)
    conn.commit()

    set_meta(conn, 'built_at', datetime.now().isoformat())
    set_meta(conn, 'type', 'historical')
    set_meta(conn, 'companies', 'AYER,CORR,QS')
    conn.close()
    print(f'\n  Historical DB built in {time.time()-t0:.1f}s → {DB_HIST.name}')

def build_live(exports_dir, fallback_dir=None):
    """Build indelco_live.db from INDELCO files — run daily."""
    print(f'\n{"="*55}')
    print('  Building live database (Indelco Plastics)')
    print(f'{"="*55}')
    t0 = time.time()

    if DB_LIVE.exists():
        DB_LIVE.unlink()

    conn = sqlite3.connect(DB_LIVE)
    build_schema(conn)

    # Search order: primary --exports first, --fallback after. Lets BC_Exports
    # supply what it has and SSMS_Exports fill in tables BC hasn't published.
    search_dirs = [exports_dir] + ([Path(fallback_dir)] if fallback_dir else [])
    show_src = fallback_dir is not None  # only annotate when there's more than one source

    print('\n  ── Indelco Plastics ──')
    for pattern, fn in [
        ('Items_INDELCO_*.csv',      lambda f: import_items(conn, f, 'INDELCO')),
        ('ILE_INDELCO_*.csv',        lambda f: import_ile(conn, f, 'INDELCO')),
        ('ValueEntry_INDELCO_*.csv', lambda f: import_value_entries(conn, f, 'INDELCO')),
        ('PO_INDELCO_*.csv',         lambda f: import_po(conn, f)),
        ('QoH_INDELCO_*.csv',        lambda f: import_qoh(conn, f)),
        ('Locations_INDELCO_*.csv',  lambda f: import_locations(conn, f)),
        ('Customers_INDELCO_*.csv',  lambda f: import_customers(conn, f)),
        ('Vendors_INDELCO_*.csv',    lambda f: import_vendors(conn, f)),
        ('ItemVendor_INDELCO_*.csv', lambda f: import_item_vendor(conn, f)),
        ('ReturnShipments_INDELCO_*.csv', lambda f: import_return_shipments(conn, f)),
        ('ObservedLT_INDELCO_*.csv', lambda f: import_observed_lt(conn, f)),
        ('SalesLines_INDELCO_*.csv', lambda f: import_sales_lines(conn, f, 'Indelco Plastics')),
    ]:
        f, src = find_csv(pattern, search_dirs)
        if f:
            label = pattern.split('_')[0]
            result = fn(f)
            if result is not None:
                tag = f' [{src}]' if show_src else ''
                print(f'  {label}: {Path(f).name}{tag} → {result:,} rows')
        else: print(f'  (no {pattern} — skipping)')

    # Ensure all location codes from ILE are in locations table
    conn.execute("""
        INSERT OR IGNORE INTO locations(location_code, is_active)
        SELECT DISTINCT location_code, 1
        FROM ile_transactions
        WHERE location_code IS NOT NULL AND location_code != ''
    """)
    loc_count = conn.execute('SELECT COUNT(*) FROM locations').fetchone()[0]
    print(f'  Locations populated: {loc_count}')
    conn.commit()

    set_meta(conn, 'built_at', datetime.now().isoformat())
    set_meta(conn, 'type', 'live')
    set_meta(conn, 'companies', 'INDELCO')
    conn.close()
    print(f'\n  Live DB built in {time.time()-t0:.1f}s → {DB_LIVE.name}')

def merge_databases():
    """Merge historical + live into indelco.db that the HTML app loads."""
    print(f'\n{"="*55}')
    print('  Merging into indelco.db')
    print(f'{"="*55}')
    t0 = time.time()

    if DB_COMBINED.exists():
        try:
            DB_COMBINED.unlink()
        except Exception as e:
            print(f'  WARNING: could not delete old indelco.db: {e}')
            print('  Close the browser and try again')
            return

    # Build combined DB fresh — never copy (avoids WAL corruption)
    live_conn = sqlite3.connect(f'file:{DB_LIVE}?mode=ro', uri=True)
    live_conn.row_factory = sqlite3.Row

    conn = sqlite3.connect(DB_COMBINED)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=-128000')
    conn.execute('PRAGMA temp_store=MEMORY')

    # Copy schema from live DB
    schema_rows = live_conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','index') ORDER BY rootpage"
    ).fetchall()
    for row in schema_rows:
        try:
            conn.execute(row[0])
        except Exception:
            pass
    conn.commit()

    # Copy all tables from live
    live_tables = [r[0] for r in live_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]

    for table in live_tables:
        try:
            rows = live_conn.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                continue
            cols = [d[0] for d in live_conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
            col_str = ','.join(f'"{c}"' for c in cols)
            placeholders = ','.join(['?'] * len(cols))
            conn.execute('BEGIN')
            conn.executemany(
                f'INSERT OR IGNORE INTO "{table}"({col_str}) VALUES({placeholders})',
                [tuple(r) for r in rows]
            )
            conn.execute('COMMIT')
            print(f'  Copied {table}: {len(rows):,} rows')
        except Exception as e:
            print(f'  Skipped {table}: {e}')

    live_conn.close()

    # Merge historical data if available
    if not DB_HIST.exists():
        print('  No historical DB — indelco.db contains live data only')
    else:
        try:
            hist_conn = sqlite3.connect(f'file:{DB_HIST}?mode=ro', uri=True)
            hist_conn.row_factory = sqlite3.Row
            for table in ['items', 'ile_transactions', 'value_entries', 'locations']:
                try:
                    all_cols = [(r[1], r[5]) for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
                    cols = [c for c,pk in all_cols if not (c=='id' and pk==1)]
                    col_str = ','.join(cols)
                    placeholders = ','.join(['?'] * len(cols))
                    rows = hist_conn.execute(f'SELECT {col_str} FROM {table}').fetchall()
                    conn.execute('BEGIN')
                    added = 0
                    for row in rows:
                        try:
                            conn.execute(f'INSERT OR IGNORE INTO {table}({col_str}) VALUES({placeholders})', tuple(row))
                            added += 1
                        except Exception:
                            pass
                    conn.execute('COMMIT')
                    print(f'  Merging {table}... +{added:,} rows')
                except Exception as e:
                    print(f'  Skipped {table}: {e}')
            hist_conn.close()
        except Exception as e:
            print(f'  WARNING: could not read historical DB: {e}')

    # Final counts
    try:
        item_count = conn.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        ile_count  = conn.execute('SELECT COUNT(*) FROM ile_transactions').fetchone()[0]
        set_meta(conn, 'merged_at', datetime.now().isoformat())
        set_meta(conn, 'live_refreshed', datetime.now().strftime('%Y-%m-%d %H:%M'))
        conn.commit()
    except Exception as e:
        item_count = ile_count = 0

    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()

    size_mb = round(DB_COMBINED.stat().st_size / 1024 / 1024, 1)
    print(f'\n  indelco.db ready: {item_count:,} items · {ile_count:,} ILE rows')
    print(f'  File size: {size_mb} MB · Built in {time.time()-t0:.1f}s')
    print('  Done. App will auto-load indelco.db on next open.')


def main():
    parser = argparse.ArgumentParser(description='Indelco DB Builder')
    parser.add_argument('--mode', choices=['historical','live','full','merge'],
                        default='live')
    parser.add_argument('--exports', default=str(EXPORTS_DIR),
                        help='Primary CSV directory (default: SSMS_Exports)')
    parser.add_argument('--fallback', default=None,
                        help='Secondary CSV directory used per-table when '
                             '--exports has no matching CSV. Typical hybrid mode: '
                             '--exports BC_Exports --fallback SSMS_Exports')
    args = parser.parse_args()

    exports = Path(args.exports)
    if not exports.exists():
        print(f'ERROR: --exports folder not found at {exports}')
        print('Run the data pull first.')
        sys.exit(1)
    if args.fallback and not Path(args.fallback).exists():
        print(f'ERROR: --fallback folder not found at {args.fallback}')
        sys.exit(1)

    if args.mode == 'historical':
        build_historical(exports, args.fallback)
        merge_databases()
    elif args.mode == 'live':
        build_live(exports, args.fallback)
        merge_databases()
    elif args.mode == 'full':
        build_historical(exports, args.fallback)
        build_live(exports, args.fallback)
        merge_databases()
    elif args.mode == 'merge':
        merge_databases()

    print(f'\n  Done. App will auto-load indelco.db on next open.')

if __name__ == '__main__':
    main()
