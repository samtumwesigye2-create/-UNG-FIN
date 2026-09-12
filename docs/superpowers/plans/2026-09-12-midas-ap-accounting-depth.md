# UNG-MIDAS AP & Accounting Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade UNG-MIDAS 0.3.0 into the authoritative supplier-invoice, Accounts Payable, double-entry accounting, and payment-status system for the UNG procurement chain.

**Architecture:** Keep PROCURE authoritative for three-way match decisions and VECTOR authoritative for physical receipts. MIDAS persists supplier invoices, accounting documents, AP vendor items, tax/withholding amounts, payment eligibility/status, and immutable financial history in PostgreSQL; NEXUS carries idempotent inbound/outbound events through a persistent outbox.

**Tech Stack:** Python 3, FastAPI, Pydantic, SQLAlchemy + PostgreSQL/psycopg, JANUS authorization, NEXUS event transport, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-midas-ap-accounting-depth-design.md`

## Global Constraints

- Preserve `/`, `/health`, `/ready`, `/v1/system`, `/v1/ledger`, and `/v1/nexus/*` compatibility.
- Target service version is `0.4.0`.
- All new monetary values use Python `Decimal` and PostgreSQL `NUMERIC(18,4)`.
- PROCURE remains the only three-way-match authority; MIDAS consumes match results and must not recompute them.
- VECTOR remains the physical receipt/inventory authority.
- Vendor invoice uniqueness is `(vendor_id, invoice_ref)`.
- Inbound event uniqueness is `(source_system, source_event_id)`.
- Posted accounting history is append-only; corrections use reversing documents.
- Every posted accounting document must balance exactly.
- AP open amount must never be negative and payment cannot exceed open balance.
- Blocked/reversed invoices cannot become paid.
- Outbound finance events use a persistent outbox; financial commits do not depend on synchronous NEXUS delivery.
- Do not implement bank transfers, bank reconciliation, AR, payroll, fixed assets, budgeting, consolidation, or a statutory tax-rate engine in this phase.

---

## File Structure

- Create `ap.py` — supplier invoices, invoice lines, AP vendor items, payment lifecycle, routes.
- Create `accounting.py` — journal documents/lines, balance validation, posting, reversal, trial balance.
- Create `midas_events.py` — persistent inbound event idempotency, procurement commitment projection, match-result processing, persistent outbox.
- Modify `storage.py` — expose safe DB transaction helpers while preserving existing `records/outbox` compatibility.
- Modify `nexus_bridge.py` — preserve `/v1/nexus/inbound` shape but delegate recognized finance events to persistent processing.
- Modify `app.py` — version 0.4.0, initialization, capabilities.
- Modify `entrypoint.py` — mount new routers.
- Create `tests/test_ap.py`.
- Create `tests/test_accounting.py`.
- Create `tests/test_midas_events.py`.
- Create `tests/test_purchase_to_pay_flow.py`.
- Separate IAM repo change: `UNG-IAM/ecosystem_permissions.py` — register MIDAS permissions.

---

### Task 1: Persistent Accounting Schema and Balanced Journal Engine

**Files:**
- Create: `accounting.py`
- Create: `tests/test_accounting.py`
- Modify: `storage.py`

**Interfaces:**
- Produces `init_accounting()`, `validate_balanced(lines) -> bool`, `post_document(...) -> dict`, `reverse_document(document_id, ...) -> dict`, accounting router.
- Later tasks consume `post_document` and `reverse_document`.

- [ ] **Step 1: Write failing balance tests**

```python
from decimal import Decimal
from accounting import validate_balanced


def test_balanced_document_passes():
    lines = [
        {'debit': Decimal('100.00'), 'credit': Decimal('0')},
        {'debit': Decimal('0'), 'credit': Decimal('100.00')},
    ]
    assert validate_balanced(lines) is True


def test_unbalanced_document_fails():
    lines = [
        {'debit': Decimal('100.00'), 'credit': Decimal('0')},
        {'debit': Decimal('0'), 'credit': Decimal('99.99')},
    ]
    assert validate_balanced(lines) is False
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_accounting.py -v`
Expected: import failure because `accounting.py` does not exist.

- [ ] **Step 3: Implement accounting tables**

`init_accounting()` creates:

```sql
CREATE TABLE IF NOT EXISTS midas_accounting_documents(
  id UUID PRIMARY KEY,
  document_type TEXT NOT NULL,
  reference TEXT NOT NULL,
  source_system TEXT NOT NULL,
  source_event_id TEXT NULL,
  posting_date DATE NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('posted','reversed')),
  reverses_document_id UUID NULL REFERENCES midas_accounting_documents(id),
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS midas_accounting_lines(
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES midas_accounting_documents(id),
  line_no INTEGER NOT NULL,
  account_code TEXT NOT NULL,
  debit NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(debit>=0),
  credit NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(credit>=0),
  vendor_id TEXT NULL,
  cost_center_ref TEXT NULL,
  tax_code TEXT NULL,
  reference TEXT NULL,
  UNIQUE(document_id,line_no)
);
```

Add database triggers that reject UPDATE/DELETE on posted document headers and accounting lines.

- [ ] **Step 4: Implement posting rules**

`validate_balanced` sums `Decimal(debit)` and `Decimal(credit)` and requires exact equality. `post_document` rejects empty lines, negative amounts, lines with both debit and credit positive, and unbalanced totals. It inserts header and all lines in one transaction and returns header + lines.

- [ ] **Step 5: Implement reversals**

`reverse_document` loads the original posted document, creates a new document with every debit/credit swapped, sets `reverses_document_id`, then marks the original header status `reversed` only as part of the allowed reversal transaction path. Historical lines remain untouched.

- [ ] **Step 6: Implement read routes**

```text
GET /v1/accounting/documents
GET /v1/accounting/documents/{document_id}
GET /v1/accounting/trial-balance
```

Use `midas.accounting.read`.

- [ ] **Step 7: Verify GREEN**

Run:
```bash
pytest tests/test_accounting.py -v
python -m py_compile accounting.py storage.py
```
Expected: all tests pass; compile exits 0.

- [ ] **Step 8: Commit**

```bash
git add accounting.py storage.py tests/test_accounting.py
git commit -m "feat: add persistent double-entry accounting"
```

---

### Task 2: Supplier Invoices and AP Vendor Open Items

**Files:**
- Create: `ap.py`
- Create: `tests/test_ap.py`
- Modify: `accounting.py`

**Interfaces:**
- Produces `init_ap()`, `create_supplier_invoice(...)`, `create_ap_item(...)`, AP router.
- Consumes `post_document` from Task 1.

- [ ] **Step 1: Write failing invoice validation tests**

```python
from decimal import Decimal
from ap import validate_invoice_totals


def test_invoice_total_matches_components():
    assert validate_invoice_totals(
        Decimal('100'), Decimal('10'), Decimal('5'), Decimal('105')
    ) is True


def test_invoice_total_mismatch_rejected():
    assert validate_invoice_totals(
        Decimal('100'), Decimal('10'), Decimal('5'), Decimal('106')
    ) is False
```

Formula: `total_amount = subtotal + tax_amount - withholding_amount`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_ap.py -v`
Expected: import failure because `ap.py` does not exist.

- [ ] **Step 3: Implement supplier invoice schema**

Create `midas_supplier_invoices` and `midas_supplier_invoice_lines` using the exact fields/status groups from the design spec. Enforce unique `(vendor_id, invoice_ref)`. All monetary columns are `NUMERIC(18,4)`.

- [ ] **Step 4: Implement AP item schema**

```sql
CREATE TABLE IF NOT EXISTS midas_ap_items(
  id UUID PRIMARY KEY,
  vendor_id TEXT NOT NULL,
  invoice_id UUID UNIQUE NOT NULL REFERENCES midas_supplier_invoices(id),
  document_ref TEXT NOT NULL,
  original_amount NUMERIC(18,4) NOT NULL CHECK(original_amount>=0),
  open_amount NUMERIC(18,4) NOT NULL CHECK(open_amount>=0),
  currency TEXT NOT NULL,
  due_date DATE NULL,
  status TEXT NOT NULL CHECK(status IN ('open','partially_paid','cleared','reversed')),
  cleared_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 5: Implement invoice routes**

```text
POST /v1/ap/invoices
GET  /v1/ap/invoices
GET  /v1/ap/invoices/{invoice_id}
POST /v1/ap/invoices/{invoice_id}/cancel
```

A new invoice starts with `match_status='pending_match'`, `accounting_status='draft'`, `payment_status='blocked'`. Creating an invoice does **not** create AP liability yet.

- [ ] **Step 6: Implement AP views**

```text
GET /v1/ap/open-items
GET /v1/ap/cleared-items
GET /v1/ap/vendors/{vendor_id}/items
```

Use `midas.ap.read`/`midas.ap.write`.

- [ ] **Step 7: Verify GREEN**

Run:
```bash
pytest tests/test_ap.py -v
python -m py_compile ap.py accounting.py
```
Expected: all tests pass; compile exits 0.

- [ ] **Step 8: Commit**

```bash
git add ap.py accounting.py tests/test_ap.py
git commit -m "feat: add supplier invoices and accounts payable"
```

---

### Task 3: PROCURE Match Consumption and Persistent NEXUS Events

**Files:**
- Create: `midas_events.py`
- Create: `tests/test_midas_events.py`
- Modify: `nexus_bridge.py`
- Modify: `storage.py`

**Interfaces:**
- Produces `init_midas_events()`, `process_inbound_event(envelope) -> dict`, `enqueue_outbox(...)`, `mark_outbox_delivered(...)`.
- Consumes `post_document`, AP invoice/item functions.

- [ ] **Step 1: Write failing idempotency tests**

```python
from midas_events import inbound_event_key


def test_inbound_event_key_is_stable():
    assert inbound_event_key('UNG-PROCURE','evt-1') == 'UNG-PROCURE:evt-1'
```

Add behavior tests proving the same `PROCURE.MATCH.PASSED` event cannot create more than one journal/AP item.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_midas_events.py -v`
Expected: import failure because `midas_events.py` does not exist.

- [ ] **Step 3: Add persistent event tables**

```sql
CREATE TABLE IF NOT EXISTS midas_inbound_events(
  source_system TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  message_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(source_system,source_event_id)
);

CREATE TABLE IF NOT EXISTS midas_procurement_commitments(
  order_id TEXT PRIMARY KEY,
  vendor_id TEXT NULL,
  amount NUMERIC(18,4) NOT NULL DEFAULT 0,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  source_event_id TEXT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS midas_event_outbox(
  id UUID PRIMARY KEY,
  idempotency_key TEXT UNIQUE NOT NULL,
  target_system TEXT NOT NULL,
  message_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  delivery_status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  delivered_at TIMESTAMPTZ NULL
);
```

- [ ] **Step 4: Implement event handlers**

`process_inbound_event` handles:

```text
PROCURE.PURCHASE_ORDER.AWARDED
PROCURE.MATCH.PASSED
PROCURE.MATCH.BLOCKED
PROCURE.SUPPLIER_INVOICE.CANCELLED
```

Rules:
- PO awarded => upsert procurement commitment only.
- MATCH.BLOCKED => invoice `match_status='blocked'`, payment stays blocked, no journal/AP item created.
- MATCH.PASSED => invoice match status updated, post exactly one balanced document, create exactly one AP item, set accounting posted, set payment eligible, queue `MIDAS.ACCOUNTING.POSTED` and `MIDAS.PAYMENT.STATUS`.
- cancellation/reversal => invoke reversal flow and set invoice/payment/AP states accordingly.

- [ ] **Step 5: Preserve NEXUS route compatibility**

Keep `POST /v1/nexus/inbound` and `GET /v1/nexus/status`, but replace transient `_events` as the finance source of truth with persistent event processing. Preserve JANUS bearer validation and wrong-target protection.

- [ ] **Step 6: Queue outbound invoice-recorded event**

Successful `POST /v1/ap/invoices` queues:

```text
MIDAS.SUPPLIER_INVOICE.RECORDED -> UNG-PROCURE
```

No invoice transaction is rolled back because NEXUS is temporarily unavailable.

- [ ] **Step 7: Verify GREEN**

Run:
```bash
pytest tests/test_midas_events.py -v
python -m py_compile midas_events.py nexus_bridge.py storage.py
```
Expected: all tests pass; compile exits 0.

- [ ] **Step 8: Commit**

```bash
git add midas_events.py nexus_bridge.py storage.py tests/test_midas_events.py
git commit -m "feat: consume procure match events persistently"
```

---

### Task 4: Payment Eligibility, Scheduling, Settlement, and AP Clearing

**Files:**
- Modify: `ap.py`
- Modify: `tests/test_ap.py`
- Modify: `midas_events.py`

**Interfaces:**
- Produces `schedule_payment(invoice_id, ...)`, `mark_paid(invoice_id, amount, ...)`, payment routes.

- [ ] **Step 1: Add failing state-machine tests**

Required cases:
- blocked invoice cannot schedule;
- eligible invoice can become scheduled;
- payment amount greater than AP open amount is rejected;
- partial payment produces `partially_paid` and reduces open amount;
- final payment produces `paid` + AP `cleared` with open amount zero;
- reversed/cancelled invoice cannot be paid.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_ap.py -v -k payment`
Expected: failures because payment transition helpers do not exist.

- [ ] **Step 3: Implement payment state projection**

Create `midas_payment_events` as append-only payment lifecycle history with invoice id, AP item id, event type, amount, currency, external payment reference, actor/source, and timestamp.

- [ ] **Step 4: Implement payment routes**

```text
GET  /v1/ap/payments
POST /v1/ap/payments/{invoice_id}/schedule
POST /v1/ap/payments/{invoice_id}/mark-paid
```

Use `midas.payments.read/write`. `mark-paid` updates AP open amount atomically and queues `MIDAS.PAYMENT.STATUS` to PROCURE.

- [ ] **Step 5: Verify GREEN**

Run:
```bash
pytest tests/test_ap.py -v
python -m py_compile ap.py midas_events.py
```
Expected: all tests pass; compile exits 0.

- [ ] **Step 6: Commit**

```bash
git add ap.py midas_events.py tests/test_ap.py
git commit -m "feat: add AP payment lifecycle and clearing"
```

---

### Task 5: Wire MIDAS 0.4.0, Capabilities, and JANUS Permissions

**Files:**
- Modify: `app.py`
- Modify: `entrypoint.py`
- Modify: `nexus_bridge.py`
- Separate repo modify: `samtumwesigye2-create/UNG-IAM:ecosystem_permissions.py`

**Interfaces:**
- Consumes initialization/router functions from Tasks 1-4.

- [ ] **Step 1: Wire database initialization**

On startup initialize accounting, AP, and event schemas without removing existing generic `records/outbox` initialization.

- [ ] **Step 2: Mount routers**

Mount accounting and AP routers in `entrypoint.py` while retaining existing finance KPI/NEXUS mounts.

- [ ] **Step 3: Bump version and capabilities**

Set MIDAS version to `0.4.0` and add exactly these `/v1/system` capabilities:

```text
supplier-invoices
accounts-payable
vendor-open-items
vendor-cleared-items
double-entry-accounting
immutable-accounting-history
tax-withholding-accounting
procure-match-consumer
payment-eligibility
payment-status
persistent-outbox
idempotent-finance-events
```

Retain existing `ledger` and `nexus-procurement-handoff` capabilities.

- [ ] **Step 4: Register JANUS permissions in UNG-IAM**

Add:

```text
midas.ap.read
midas.ap.write
midas.accounting.read
midas.accounting.post
midas.payments.read
midas.payments.write
```

Preserve existing MIDAS permissions and grant new names to platform-admin through existing seeding behavior.

- [ ] **Step 5: Compile verification**

Run:
```bash
python -m py_compile app.py entrypoint.py ap.py accounting.py midas_events.py nexus_bridge.py storage.py
```
Expected: exit 0.

- [ ] **Step 6: Commit MIDAS wiring and IAM permission change separately**

MIDAS commit:
```bash
git add app.py entrypoint.py nexus_bridge.py
git commit -m "feat: wire MIDAS 0.4 AP accounting platform"
```

IAM commit:
```bash
git add ecosystem_permissions.py
git commit -m "feat: register MIDAS AP accounting permissions"
```

---

### Task 6: End-to-End Purchase-to-Pay Acceptance and Production Deployment

**Files:**
- Create: `tests/test_purchase_to_pay_flow.py`
- Modify as needed only to satisfy failed acceptance tests.

**Interfaces:**
- Consumes all prior task APIs/helpers.

- [ ] **Step 1: Write controlled end-to-end test**

The test must prove in one scenario:
1. PO award commitment arrives;
2. supplier invoice + lines recorded with tax and withholding;
3. duplicate vendor invoice is rejected/idempotently recognized;
4. blocked match creates no AP posting;
5. passed match creates exactly one balanced accounting document and one AP open item;
6. duplicate match-passed event creates no second posting;
7. invoice becomes eligible, then scheduled;
8. partial payment reduces open balance;
9. final payment clears AP item;
10. reversal creates a distinct reversing journal and leaves historical lines unchanged.

Use local test doubles for outbound NEXUS delivery; unit/integration tests must not call production endpoints.

- [ ] **Step 2: Run acceptance test**

Run: `pytest tests/test_purchase_to_pay_flow.py -v`
Expected final state: PASS.

- [ ] **Step 3: Run full MIDAS tests**

Run: `pytest -v`
Expected: zero failures.

- [ ] **Step 4: Run compile verification**

Run:
```bash
python -m py_compile app.py entrypoint.py ap.py accounting.py midas_events.py nexus_bridge.py storage.py finance_kpis.py
```
Expected: exit 0.

- [ ] **Step 5: Review changed files before merge**

Verify no code recomputes PROCURE's three-way match, no new money fields use float, historical journal lines are immutable, and no synchronous NEXUS call is required to commit financial state.

- [ ] **Step 6: Create and merge MIDAS PR after review**

PR title:
```text
Complete UNG-MIDAS AP and supplier accounting depth
```

- [ ] **Step 7: Create and merge IAM permission PR after review**

PR title:
```text
Register UNG-MIDAS AP accounting permissions
```

- [ ] **Step 8: Validate Railway production**

For UNG-MIDAS:
- deployment status `SUCCESS`;
- deployed source commit equals merged PR commit;
- `/health` returns HTTP 200 and version `0.4.0`;
- `/ready` remains ready/database connected;
- `/v1/system` advertises all new capabilities;
- startup logs contain no migration/schema exceptions;
- no unexpected HTTP 5xx after deploy;
- new AP/accounting routers exist in the deployed container/OpenAPI.

For UNG-IAM:
- deployment status `SUCCESS`;
- deployed source commit equals merged IAM PR commit;
- `/health` remains HTTP 200;
- deployed `ecosystem_permissions.py` contains and seeds all six new MIDAS permissions.

- [ ] **Step 9: Run one controlled authenticated production acceptance flow if service credentials are available**

Prove PO award -> invoice -> match passed -> accounting posted -> AP open item -> eligible payment projection. Do not execute real bank payment rails.

---

## Self-Review Checklist

- Spec coverage: invoice header/lines, AP items, balanced journals, reversals, tax/withholding, payment status, idempotency, persistent outbox, PROCURE match consumption, JANUS permissions, compatibility, production validation all map to explicit tasks.
- Placeholder scan: no TBD/TODO/implement-later steps remain.
- Type consistency: all introduced monetary fields use `Decimal`/`NUMERIC(18,4)`.
- Ownership consistency: PROCURE owns match decisions; VECTOR owns physical receipts; MIDAS owns accounting/AP/payment status.
- Non-goals remain excluded: no banking execution, AR, payroll, fixed assets, budgeting, reconciliation, consolidation, or tax-rate engine.
