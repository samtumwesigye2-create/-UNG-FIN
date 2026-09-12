# UNG-MIDAS AP & Accounting Depth Design

## Purpose
Extend UNG-MIDAS from a thin finance handoff/ledger service into the authoritative Accounts Payable and supplier-accounting system for the UNG procurement chain, while preserving current routes and keeping UNG-PROCURE as the three-way-match authority.

## Existing State
- UNG-MIDAS currently identifies as version 0.3.0 and exposes a minimal in-memory ledger API.
- PostgreSQL is already available through `storage.py`, currently with generic `records` and `outbox` tables.
- NEXUS inbound exists and currently recognizes `PROCURE.PURCHASE_ORDER.AWARDED` as a finance commitment handoff.
- JANUS authorization exists through the existing MIDAS integration path.

## Architectural Boundaries
- **UNG-PROCURE** remains authoritative for requisitions, RFQs, purchase orders, goods-receipt projections, invoice-match decisions, and match exceptions.
- **UNG-VECTOR** remains authoritative for physical goods receipt, inventory, batches/lots, serials, and warehouse truth.
- **UNG-MIDAS** becomes authoritative for supplier invoices as accounting documents, AP/vendor open items, journal entries, tax/withholding accounting, payment eligibility/status, and financial audit history.
- **NEXUS** remains the inter-system event backbone.
- **JANUS** remains the authorization authority.

MIDAS must never recompute PROCURE's three-way match independently. It consumes the authoritative match result and converts that result into financial state.

## Design Approach
Use dedicated PostgreSQL accounting tables and focused modules rather than continuing to place accounting state into the generic JSON `records` table. Existing compatibility routes remain available, but new finance/AP functionality uses `Decimal` in Python and PostgreSQL `NUMERIC` for all monetary values.

## Core Financial Objects

### 1. Supplier Invoice Header
Fields:
- id (UUID)
- source_event_id (unique inbound idempotency key)
- invoice_ref (vendor invoice number; unique per vendor)
- vendor_id
- purchase_order_id / external order reference
- invoice_date
- posting_date
- currency
- subtotal
- tax_amount
- withholding_amount
- total_amount
- payment_terms
- due_date
- match_status
- accounting_status
- payment_status
- created_at / updated_at

Statuses are intentionally separated:
- `match_status`: pending_match, matched, matched_with_tolerance, blocked
- `accounting_status`: draft, posted, reversed
- `payment_status`: blocked, eligible, scheduled, paid, cancelled

### 2. Supplier Invoice Lines
Fields:
- id
- invoice_id
- line_no
- sku / description
- quantity
- unit_price
- line_subtotal
- tax_code
- tax_amount
- withholding_code
- withholding_amount
- expense_or_inventory_account
- cost_center_ref / account_assignment_ref

### 3. AP Vendor Items
A vendor item represents the payable balance created by a posted supplier invoice.
Fields:
- id
- vendor_id
- invoice_id
- document_ref
- original_amount
- open_amount
- currency
- due_date
- status: open, partially_paid, cleared, reversed
- cleared_at

This is MIDAS's equivalent to open/cleared vendor-item views.

### 4. Accounting Documents / Journal Entries
Each financial posting creates one immutable accounting document header with balanced lines.

Header:
- document_id
- document_type
- reference
- source_system
- source_event_id
- posting_date
- currency
- status
- created_at

Lines:
- document_id
- line_no
- account_code
- debit
- credit
- vendor_id / cost_center / tax_code / reference metadata

Rule: sum(debit) == sum(credit) for every posted document. Database/application validation must reject an unbalanced document.

### 5. Tax and Withholding
MIDAS stores tax and withholding values and accounting classifications on invoice lines and journals. The first implementation does not attempt to become a national tax engine; it records the codes/rates/amounts supplied by approved upstream workflows and produces separate tax/withholding journal lines when configured.

### 6. Payment Eligibility & Status
MIDAS does not need to execute banking payments in this phase. It must own payment eligibility and lifecycle state.

Rules:
- new supplier invoice: payment blocked
- `PROCURE.MATCH.PASSED`: match_status becomes matched/matched_with_tolerance and payment_status becomes eligible after successful accounting posting
- `PROCURE.MATCH.BLOCKED`: payment_status remains blocked
- cancelled/reversed invoice: never eligible
- payment status transitions are auditable and idempotent

Payment states:
`blocked -> eligible -> scheduled -> paid`
with `cancelled` terminal where appropriate.

## NEXUS Event Contracts

### Inbound to MIDAS
- `PROCURE.PURCHASE_ORDER.AWARDED`
  - create/update a procurement commitment reference only; do not create AP liability.
- `PROCURE.MATCH.PASSED`
  - find supplier invoice by invoice/invoice projection reference.
  - mark match passed.
  - post accounting document if not already posted.
  - create AP open item.
  - set payment_status `eligible`.
  - processing must be idempotent by message/event id.
- `PROCURE.MATCH.BLOCKED`
  - preserve invoice but set match/payment state blocked.
  - do not create payable posting if not already posted.
- supplier invoice cancellation/reversal event from PROCURE/NEXUS
  - reverse posted journal with a separate reversal document; never UPDATE/DELETE historical journal lines.

### Outbound from MIDAS
- `MIDAS.SUPPLIER_INVOICE.RECORDED`
  - informs PROCURE a supplier invoice exists and supplies the accounting-side invoice reference.
- `MIDAS.PAYMENT.STATUS`
  - sends eligible/scheduled/paid/cancelled state back to PROCURE.
- `MIDAS.ACCOUNTING.POSTED`
  - optional operational event after successful journal/AP posting.

All outbound events use a persistent outbox and idempotency key; no financial transaction should depend on a synchronous external call succeeding.

## API Surface

### Supplier invoices
- `POST /v1/ap/invoices`
- `GET /v1/ap/invoices`
- `GET /v1/ap/invoices/{invoice_id}`
- `POST /v1/ap/invoices/{invoice_id}/cancel`

### AP vendor items
- `GET /v1/ap/open-items`
- `GET /v1/ap/cleared-items`
- `GET /v1/ap/vendors/{vendor_id}/items`

### Accounting documents
- `GET /v1/accounting/documents`
- `GET /v1/accounting/documents/{document_id}`
- `GET /v1/accounting/trial-balance`

### Payment status
- `GET /v1/ap/payments`
- `POST /v1/ap/payments/{invoice_id}/schedule`
- `POST /v1/ap/payments/{invoice_id}/mark-paid`

The phase tracks payment state only; it does not initiate bank transfers.

## Authorization
Use JANUS permissions, registered in UNG-IAM:
- `midas.ap.read`
- `midas.ap.write`
- `midas.accounting.read`
- `midas.accounting.post`
- `midas.payments.read`
- `midas.payments.write`
- existing `midas.ledger.read/post` remains for backward compatibility

Platform admin remains an override.

## Data Integrity Rules
- All new money fields: Python `Decimal`, PostgreSQL `NUMERIC(18,4)` or tighter where justified.
- Vendor invoice uniqueness: `(vendor_id, invoice_ref)`.
- Inbound event uniqueness: `(source_system, source_event_id)`.
- Every posted journal must balance exactly.
- Accounting history is append-only: no UPDATE/DELETE of posted journal headers/lines; corrections use reversal documents.
- AP open amount may never be negative.
- A payment cannot exceed the current open AP balance.
- A blocked or reversed invoice cannot transition to paid.
- Repeated NEXUS events must not duplicate invoices, journals, AP items, or payments.

## Compatibility
- Preserve `/`, `/health`, `/ready`, `/v1/system`, `/v1/ledger`, and `/v1/nexus/*` routes.
- Do not remove current generic storage in this phase; new finance objects use dedicated tables.
- Legacy in-memory ledger stays temporarily for compatibility, but `/v1/system` must distinguish it from the new persistent accounting document subsystem.

## Versioning
Target service version: **UNG-MIDAS 0.4.0**.

New `/v1/system` capabilities:
- supplier-invoices
- accounts-payable
- vendor-open-items
- vendor-cleared-items
- double-entry-accounting
- immutable-accounting-history
- tax-withholding-accounting
- procure-match-consumer
- payment-eligibility
- payment-status
- persistent-outbox
- idempotent-finance-events

## Implementation Modules
Recommended focused files:
- `ap.py` — supplier invoices, AP items, payment state
- `accounting.py` — journal documents, balance validation, trial balance, reversals
- `midas_events.py` — persistent inbound-event processing and NEXUS outbox behavior
- `tests/test_ap.py`
- `tests/test_accounting.py`
- `tests/test_midas_events.py`
- `tests/test_purchase_to_pay_flow.py`

Modify:
- `app.py` — version/capabilities, initialization, router wiring if needed
- `entrypoint.py` — router mounting
- `storage.py` — database helper/migration initialization only if required
- `nexus_bridge.py` — replace/extend transient finance-event handling with persistent event delegation while preserving current route shape
- `UNG-IAM/ecosystem_permissions.py` — register MIDAS permissions in the separate IAM repo

## Acceptance Criteria
A controlled purchase-to-pay accounting scenario must prove:
1. procurement PO award arrives and is idempotently recorded as a commitment reference;
2. supplier invoice is recorded with lines, tax, withholding, and blocked payment status;
3. duplicate vendor invoice/reference is rejected or idempotently recognized;
4. `PROCURE.MATCH.BLOCKED` keeps the invoice blocked and creates no AP payable posting;
5. `PROCURE.MATCH.PASSED` creates exactly one balanced accounting document and one AP open item;
6. repeated match-passed event creates no duplicate posting;
7. payment can move eligible -> scheduled -> paid without exceeding open balance;
8. paid/cleared AP item moves from open to cleared view;
9. invoice cancellation/reversal creates a reversing journal rather than altering historical journal lines;
10. `/health` and `/ready` stay green after production deployment;
11. Railway production source commit matches the merged GitHub commit;
12. JANUS contains and seeds all new MIDAS permissions.

## Explicit Non-Goals for 0.4.0
- bank/payment-rail execution
- bank reconciliation
- customer Accounts Receivable
- payroll accounting
- fixed-asset accounting
- full statutory tax-rate engine
- budgeting/forecasting
- multi-entity consolidation

Those may be future MIDAS phases; they are not required for this procurement/AP completion.
