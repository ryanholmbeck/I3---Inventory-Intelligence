# Backlog Review — Data Spec & Extract Design

The app reads **read-only order data** (open sales order lines, enriched) and
joins it to **review decisions** stored in Supabase. This doc defines the
canonical order-line shape every source (Indelco BC, Centrex DDI, QS) maps
into, how today's 51-column `Source_Sales_Lines` maps to it, and the Jet
Report design to make the BC feed self-service.

## Canonical order-line schema (source-agnostic)

One row per open sales-order line. The app summarizes these to one row per
order (Document No.) for the review grid.

| Canonical field | Type | Notes |
|---|---|---|
| source_system | text | INDELCO_BC / CENTREX_DDI / QS |
| document_no | text | order number (unique across sources via prefix) |
| line_no | int | |
| customer_no | text | Sell-to Customer No. |
| location_code | text | → budget_group via bl_budget_group_map |
| branch_name | text | |
| salesperson | text | `DOMAIN\user` — matches workstation identity |
| buyer_name | text | |
| vendor_no / vendor_name | text | |
| item_no | text | |
| description | text | |
| quantity | number | |
| outstanding_quantity | number | drives "still open" |
| uom | text | |
| outstanding_amount | number | **the $ that rolls up to Total Outstanding $** |
| shipment_date | date | earliest/latest per order |
| order_date | date | |
| qty_on_hand | number | for In-Stock classification |
| total_demand | number | |
| total_po | number | |
| shortage | number | |
| on_po_qty | number | |
| po_number | text | |
| po_due_date | date | |
| po_late | bool | |
| is_drop_ship | bool | |
| backlog / late | bool | flags |

### Order-level fields the app COMPUTES (don't extract)
In Stock $, Needs PO $, On PO $, Total Outstanding $, Earliest/Latest Ship
Date, Mixed Ship Dates, Earliest/Latest PO Due, Line Count, Location Codes,
Buyer/Vendor Names, Days Late, Review Priority, Budget Group.

## Mapping: current `Source_Sales_Lines` (51 cols) → canonical

Most of the 51 columns map directly; the rest are either recomputed by the
app or unused. Essentials:

```
Document No.          -> document_no          Line No.            -> line_no
Sell-to Customer No.  -> customer_no          Location Code       -> location_code
Branch Name           -> branch_name          Salesperson         -> salesperson
Buyer Name            -> buyer_name            Vendor No./Name     -> vendor_*
No.                   -> item_no              Description         -> description
Quantity              -> quantity             Outstanding Quantity-> outstanding_quantity
Unit of Measure Code  -> uom                  Outstanding Amount  -> outstanding_amount
Shipment Date         -> shipment_date         Order Date         -> order_date
Quantity on Hand      -> qty_on_hand           Total Demand       -> total_demand
Total PO              -> total_po              Shortage           -> shortage
On PO                 -> on_po_qty             PO Number          -> po_number
PO Due Date           -> po_due_date           PO Late            -> po_late
Is DS                 -> is_drop_ship          Backlog / Late     -> backlog / late
OpCo                  -> (helps set source_system)
```

Ignored (workbook-internal / recomputed): Same Day, Yesterday, Next 10 Days,
Review Date Scope, Reviewed, Tax* columns, Reserve, Item Reference No., etc.

## Feed phases

**Phase 1 (now, zero new work):** the refresh script consumes your existing
daily `Source_Sales_Lines` export (already enriched) → normalizes to the
canonical shape → loads it. Nothing new to build on the BC side.

**Phase 2 (self-service):** replace the manual manipulation with a Jet Report.

## Jet Report design (BC — Phase 2)

Goal: reproduce the enriched open-order extract directly from BC so no manual
cross-referencing is needed. Jet's `NL()` lists rows; field pulls read values.

**Row driver — open order lines:**
```
=NL("Rows","Sales Line","No.",
    "Document Type","=Order",
    "Outstanding Quantity",">0")
```

**Per-row field pulls** (fill down against the row key):
```
Document No.          =NF(row,"Document No.")
Line No.              =NF(row,"Line No.")
Sell-to Customer No.  =NF(row,"Sell-to Customer No.")
Location Code         =NF(row,"Location Code")
Item No.              =NF(row,"No.")
Description           =NF(row,"Description")
Outstanding Quantity  =NF(row,"Outstanding Quantity")
Outstanding Amount    =NF(row,"Outstanding Amount")   ' or Amt * (Outstd/Qty)
Shipment Date         =NF(row,"Shipment Date")
Salesperson (header)  =NL("First","Sales Header","Salesperson Code","No.",=NF(row,"Document No."),"Document Type","=Order")
Order Date (header)   =NL("First","Sales Header","Order Date","No.",=NF(row,"Document No."),"Document Type","=Order")
```

**Enrichment (QoH / PO) — two options:**
- *In Jet:* add lookups — QoH via `=NL("Sum","Item Ledger Entry","Quantity","Item No.",item,"Location Code",loc)`; open PO via `NL/NF` against "Purchase Line" (Outstanding Quantity>0) keyed by item+location.
- *In the refresh script (recommended):* export raw Sales Lines + a QoH pull + an open-PO pull, and let Python do the In-Stock / Needs-PO / On-PO classification. Keeps the cross-ref logic versioned and testable instead of buried in a workbook.

## Centrex (DDI) & QS

Same canonical target, different extract. DDI export → map its columns to the
canonical fields above, set `source_system=CENTREX_DDI`, and it flows through
the identical pipeline. QS similarly. All budget groups already exist in
`bl_budget_group_map`, so one file, everyone.
