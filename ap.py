from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from midas_auth import require_permission
from pydantic import BaseModel, Field
from sqlalchemy import text
from storage import transaction

router=APIRouter(prefix='/v1/ap',tags=['accounts-payable'])

def _now(): return datetime.now(timezone.utc)
def _d(v): return Decimal(str(v or 0))

def validate_invoice_totals(subtotal,tax_amount,withholding_amount,total_amount):
    return _d(subtotal)+_d(tax_amount)-_d(withholding_amount)==_d(total_amount)

def payment_transition(current_status,action,amount,open_amount):
    amount,open_amount=_d(amount),_d(open_amount)
    if action=='schedule':
        if current_status!='eligible': raise ValueError('invoice_not_payment_eligible')
        return {'payment_status':'scheduled','ap_status':'open','open_amount':open_amount}
    if action=='pay':
        if current_status not in {'eligible','scheduled'}: raise ValueError('invoice_not_payment_eligible')
        if amount<=0: raise ValueError('payment_amount_must_be_positive')
        if amount>open_amount: raise ValueError('payment_exceeds_open_balance')
        remaining=open_amount-amount
        return {'payment_status':'paid' if remaining==0 else 'scheduled','ap_status':'cleared' if remaining==0 else 'partially_paid','open_amount':remaining}
    raise ValueError('invalid_payment_action')

class InvoiceLineIn(BaseModel):
    line_no:int
    sku:str|None=None
    description:str=''
    quantity:Decimal=Decimal('1')
    unit_price:Decimal
    line_subtotal:Decimal
    tax_code:str|None=None
    tax_amount:Decimal=Decimal('0')
    withholding_code:str|None=None
    withholding_amount:Decimal=Decimal('0')
    expense_or_inventory_account:str='500000'
    cost_center_ref:str|None=None
    account_assignment_ref:str|None=None

class InvoiceIn(BaseModel):
    source_event_id:str
    invoice_ref:str
    vendor_id:str
    purchase_order_id:str|None=None
    invoice_date:date
    posting_date:date|None=None
    currency:str='USD'
    subtotal:Decimal
    tax_amount:Decimal=Decimal('0')
    withholding_amount:Decimal=Decimal('0')
    total_amount:Decimal
    payment_terms:str='NET30'
    due_date:date|None=None
    lines:list[InvoiceLineIn]=Field(default_factory=list)

class PaymentIn(BaseModel):
    amount:Decimal|None=None
    external_payment_ref:str|None=None


def init_ap():
    with transaction() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_supplier_invoices(
          id UUID PRIMARY KEY, source_event_id TEXT UNIQUE NOT NULL, invoice_ref TEXT NOT NULL,
          vendor_id TEXT NOT NULL, purchase_order_id TEXT NULL, invoice_date DATE NOT NULL,
          posting_date DATE NOT NULL, currency TEXT NOT NULL, subtotal NUMERIC(18,4) NOT NULL CHECK(subtotal>=0),
          tax_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(tax_amount>=0),
          withholding_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(withholding_amount>=0),
          total_amount NUMERIC(18,4) NOT NULL CHECK(total_amount>=0), payment_terms TEXT NOT NULL,
          due_date DATE NULL, match_status TEXT NOT NULL CHECK(match_status IN ('pending_match','matched','matched_with_tolerance','blocked')),
          accounting_status TEXT NOT NULL CHECK(accounting_status IN ('draft','posted','reversed')),
          payment_status TEXT NOT NULL CHECK(payment_status IN ('blocked','eligible','scheduled','paid','cancelled')),
          accounting_document_id UUID NULL REFERENCES midas_accounting_documents(id), created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL, UNIQUE(vendor_id,invoice_ref))'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_supplier_invoice_lines(
          id UUID PRIMARY KEY, invoice_id UUID NOT NULL REFERENCES midas_supplier_invoices(id) ON DELETE CASCADE,
          line_no INTEGER NOT NULL, sku TEXT NULL, description TEXT NOT NULL, quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0),
          unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0), line_subtotal NUMERIC(18,4) NOT NULL CHECK(line_subtotal>=0),
          tax_code TEXT NULL, tax_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(tax_amount>=0),
          withholding_code TEXT NULL, withholding_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(withholding_amount>=0),
          expense_or_inventory_account TEXT NOT NULL, cost_center_ref TEXT NULL, account_assignment_ref TEXT NULL,
          UNIQUE(invoice_id,line_no))'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_ap_items(
          id UUID PRIMARY KEY, vendor_id TEXT NOT NULL, invoice_id UUID UNIQUE NOT NULL REFERENCES midas_supplier_invoices(id),
          document_ref TEXT NOT NULL, original_amount NUMERIC(18,4) NOT NULL CHECK(original_amount>=0),
          open_amount NUMERIC(18,4) NOT NULL CHECK(open_amount>=0), currency TEXT NOT NULL, due_date DATE NULL,
          status TEXT NOT NULL CHECK(status IN ('open','partially_paid','cleared','reversed')),
          cleared_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_payment_events(
          id UUID PRIMARY KEY, invoice_id UUID NOT NULL REFERENCES midas_supplier_invoices(id),
          ap_item_id UUID NULL REFERENCES midas_ap_items(id), event_type TEXT NOT NULL,
          amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(amount>=0), currency TEXT NOT NULL,
          external_payment_ref TEXT NULL, actor_source TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)'''))
        c.execute(text("""CREATE OR REPLACE FUNCTION midas_payment_events_immutable() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'midas_payment_events_are_append_only'; END; $$ LANGUAGE plpgsql"""))
        c.execute(text('DROP TRIGGER IF EXISTS trg_midas_payment_events_immutable ON midas_payment_events'))
        c.execute(text('''CREATE TRIGGER trg_midas_payment_events_immutable BEFORE UPDATE OR DELETE ON midas_payment_events
          FOR EACH ROW EXECUTE FUNCTION midas_payment_events_immutable()'''))

def create_supplier_invoice(body:InvoiceIn):
    if not validate_invoice_totals(body.subtotal,body.tax_amount,body.withholding_amount,body.total_amount):
        raise ValueError('invoice_total_mismatch')
    if not body.lines: raise ValueError('invoice_requires_lines')
    line_subtotal=sum((_d(x.line_subtotal) for x in body.lines),Decimal('0'))
    line_tax=sum((_d(x.tax_amount) for x in body.lines),Decimal('0'))
    line_withholding=sum((_d(x.withholding_amount) for x in body.lines),Decimal('0'))
    if line_subtotal!=_d(body.subtotal) or line_tax!=_d(body.tax_amount) or line_withholding!=_d(body.withholding_amount):
        raise ValueError('invoice_line_totals_mismatch')
    iid=str(uuid4()); now=_now()
    with transaction() as c:
        duplicate=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE vendor_id=:v AND invoice_ref=:r'),{'v':body.vendor_id,'r':body.invoice_ref}).mappings().first()
        if duplicate: raise ValueError('vendor_invoice_exists')
        c.execute(text('''INSERT INTO midas_supplier_invoices
          (id,source_event_id,invoice_ref,vendor_id,purchase_order_id,invoice_date,posting_date,currency,subtotal,tax_amount,withholding_amount,total_amount,
           payment_terms,due_date,match_status,accounting_status,payment_status,accounting_document_id,created_at,updated_at)
          VALUES(:id,:seid,:ref,:vendor,:po,:idate,:pdate,:cur,:sub,:tax,:wh,:total,:terms,:due,'pending_match','draft','blocked',NULL,:now,:now)'''),
          {'id':iid,'seid':body.source_event_id,'ref':body.invoice_ref,'vendor':body.vendor_id,'po':body.purchase_order_id,'idate':body.invoice_date,
           'pdate':body.posting_date or body.invoice_date,'cur':body.currency.upper(),'sub':body.subtotal,'tax':body.tax_amount,'wh':body.withholding_amount,
           'total':body.total_amount,'terms':body.payment_terms,'due':body.due_date,'now':now})
        for line in body.lines:
            c.execute(text('''INSERT INTO midas_supplier_invoice_lines
              (id,invoice_id,line_no,sku,description,quantity,unit_price,line_subtotal,tax_code,tax_amount,withholding_code,withholding_amount,
               expense_or_inventory_account,cost_center_ref,account_assignment_ref)
              VALUES(:id,:iid,:ln,:sku,:desc,:qty,:price,:sub,:taxcode,:tax,:whcode,:wh,:acct,:cc,:aa)'''),
              {'id':str(uuid4()),'iid':iid,'ln':line.line_no,'sku':line.sku,'desc':line.description,'qty':line.quantity,'price':line.unit_price,
               'sub':line.line_subtotal,'taxcode':line.tax_code,'tax':line.tax_amount,'whcode':line.withholding_code,'wh':line.withholding_amount,
               'acct':line.expense_or_inventory_account,'cc':line.cost_center_ref,'aa':line.account_assignment_ref})
    try:
        from midas_events import enqueue_outbox
        enqueue_outbox(f'invoice-recorded:{iid}','UNG-PROCURE','MIDAS.SUPPLIER_INVOICE.RECORDED',{'invoice_id':iid,'invoice_ref':body.invoice_ref,'vendor_id':body.vendor_id,'purchase_order_id':body.purchase_order_id,'total_amount':str(body.total_amount),'currency':body.currency.upper()})
    except Exception:
        pass
    return get_invoice(iid)

def get_invoice(invoice_id):
    with transaction() as c:
        inv=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE id=:id'),{'id':invoice_id}).mappings().first()
        if not inv:return None
        lines=c.execute(text('SELECT * FROM midas_supplier_invoice_lines WHERE invoice_id=:id ORDER BY line_no'),{'id':invoice_id}).mappings().all()
        return {'invoice':dict(inv),'lines':[dict(x) for x in lines]}

def create_ap_item(invoice_id,document_ref):
    with transaction() as c:
        existing=c.execute(text('SELECT * FROM midas_ap_items WHERE invoice_id=:id'),{'id':invoice_id}).mappings().first()
        if existing:return dict(existing)
        inv=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE id=:id'),{'id':invoice_id}).mappings().first()
        if not inv: raise ValueError('invoice_not_found')
        now=_now(); item_id=str(uuid4())
        row=c.execute(text('''INSERT INTO midas_ap_items(id,vendor_id,invoice_id,document_ref,original_amount,open_amount,currency,due_date,status,cleared_at,created_at,updated_at)
          VALUES(:id,:vendor,:invoice,:ref,:amt,:amt,:cur,:due,'open',NULL,:now,:now) RETURNING *'''),
          {'id':item_id,'vendor':inv['vendor_id'],'invoice':invoice_id,'ref':document_ref,'amt':inv['total_amount'],'cur':inv['currency'],'due':inv['due_date'],'now':now}).mappings().first()
        return dict(row)

def cancel_invoice(invoice_id,source_event_id=None):
    with transaction() as c:
        inv=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE id=:id FOR UPDATE'),{'id':invoice_id}).mappings().first()
        if not inv: raise ValueError('invoice_not_found')
        doc_id=inv['accounting_document_id']
    reversal=None
    if doc_id:
        from accounting import reverse_document
        reversal=reverse_document(str(doc_id),source_event_id)
    with transaction() as c:
        c.execute(text("UPDATE midas_supplier_invoices SET accounting_status=CASE WHEN accounting_document_id IS NULL THEN accounting_status ELSE 'reversed' END,payment_status='cancelled',updated_at=:now WHERE id=:id"),{'id':invoice_id,'now':_now()})
        c.execute(text("UPDATE midas_ap_items SET status='reversed',open_amount=0,cleared_at=:now,updated_at=:now WHERE invoice_id=:id AND status<>'reversed'"),{'id':invoice_id,'now':_now()})
    return {'invoice':get_invoice(invoice_id),'reversal':reversal}

def schedule_payment(invoice_id,actor_source='api'):
    with transaction() as c:
        inv=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE id=:id FOR UPDATE'),{'id':invoice_id}).mappings().first()
        if not inv: raise ValueError('invoice_not_found')
        ap=c.execute(text('SELECT * FROM midas_ap_items WHERE invoice_id=:id FOR UPDATE'),{'id':invoice_id}).mappings().first()
        if not ap: raise ValueError('ap_item_not_found')
        state=payment_transition(inv['payment_status'],'schedule',Decimal('0'),ap['open_amount']); now=_now()
        c.execute(text("UPDATE midas_supplier_invoices SET payment_status='scheduled',updated_at=:now WHERE id=:id"),{'id':invoice_id,'now':now})
        c.execute(text('''INSERT INTO midas_payment_events(id,invoice_id,ap_item_id,event_type,amount,currency,external_payment_ref,actor_source,created_at)
          VALUES(:id,:invoice,:ap,'scheduled',0,:cur,NULL,:actor,:now)'''),{'id':str(uuid4()),'invoice':invoice_id,'ap':ap['id'],'cur':ap['currency'],'actor':actor_source,'now':now})
        return state

def mark_paid(invoice_id,amount,external_payment_ref=None,actor_source='api'):
    with transaction() as c:
        inv=c.execute(text('SELECT * FROM midas_supplier_invoices WHERE id=:id FOR UPDATE'),{'id':invoice_id}).mappings().first()
        if not inv: raise ValueError('invoice_not_found')
        ap=c.execute(text('SELECT * FROM midas_ap_items WHERE invoice_id=:id FOR UPDATE'),{'id':invoice_id}).mappings().first()
        if not ap: raise ValueError('ap_item_not_found')
        state=payment_transition(inv['payment_status'],'pay',amount,ap['open_amount']); now=_now()
        c.execute(text('UPDATE midas_supplier_invoices SET payment_status=:ps,updated_at=:now WHERE id=:id'),{'ps':state['payment_status'],'now':now,'id':invoice_id})
        c.execute(text('UPDATE midas_ap_items SET open_amount=:open,status=:status,cleared_at=:cleared,updated_at=:now WHERE id=:id'),
                  {'open':state['open_amount'],'status':state['ap_status'],'cleared':now if state['ap_status']=='cleared' else None,'now':now,'id':ap['id']})
        c.execute(text('''INSERT INTO midas_payment_events(id,invoice_id,ap_item_id,event_type,amount,currency,external_payment_ref,actor_source,created_at)
          VALUES(:id,:invoice,:ap,'paid',:amount,:cur,:ref,:actor,:now)'''),{'id':str(uuid4()),'invoice':invoice_id,'ap':ap['id'],'amount':_d(amount),'cur':ap['currency'],'ref':external_payment_ref,'actor':actor_source,'now':now})
    try:
        from midas_events import enqueue_outbox
        enqueue_outbox(f'payment-status:{invoice_id}:{external_payment_ref or str(_now().timestamp())}','UNG-PROCURE','MIDAS.PAYMENT.STATUS',{'invoice_id':invoice_id,'payment_status':state['payment_status'],'open_amount':str(state['open_amount']),'currency':ap['currency']})
    except Exception:
        pass
    return state

@router.post('/invoices',status_code=201)
def post_invoice(body:InvoiceIn,authorization:str|None=Header(None)):
    require_permission('midas.ap.write',authorization)
    try:return create_supplier_invoice(body)
    except ValueError as e: raise HTTPException(409 if str(e)=='vendor_invoice_exists' else 422,str(e))

@router.get('/invoices')
def invoices(authorization:str|None=Header(None)):
    require_permission('midas.ap.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text('SELECT * FROM midas_supplier_invoices ORDER BY created_at DESC')).mappings().all()]

@router.get('/invoices/{invoice_id}')
def invoice(invoice_id:str,authorization:str|None=Header(None)):
    require_permission('midas.ap.read',authorization); result=get_invoice(invoice_id)
    if not result: raise HTTPException(404,'invoice_not_found')
    return result

@router.post('/invoices/{invoice_id}/cancel')
def cancel(invoice_id:str,authorization:str|None=Header(None)):
    require_permission('midas.ap.write',authorization)
    try:return cancel_invoice(invoice_id)
    except ValueError as e: raise HTTPException(409,str(e))

@router.get('/open-items')
def open_items(authorization:str|None=Header(None)):
    require_permission('midas.ap.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text("SELECT * FROM midas_ap_items WHERE status IN ('open','partially_paid') ORDER BY due_date NULLS LAST,created_at")).mappings().all()]

@router.get('/cleared-items')
def cleared_items(authorization:str|None=Header(None)):
    require_permission('midas.ap.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text("SELECT * FROM midas_ap_items WHERE status='cleared' ORDER BY cleared_at DESC")).mappings().all()]

@router.get('/vendors/{vendor_id}/items')
def vendor_items(vendor_id:str,authorization:str|None=Header(None)):
    require_permission('midas.ap.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text('SELECT * FROM midas_ap_items WHERE vendor_id=:v ORDER BY created_at DESC'),{'v':vendor_id}).mappings().all()]

@router.get('/payments')
def payments(authorization:str|None=Header(None)):
    require_permission('midas.payments.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text('SELECT * FROM midas_payment_events ORDER BY created_at DESC')).mappings().all()]

@router.post('/payments/{invoice_id}/schedule')
def schedule(invoice_id:str,authorization:str|None=Header(None)):
    require_permission('midas.payments.write',authorization)
    try:return schedule_payment(invoice_id)
    except ValueError as e: raise HTTPException(409,str(e))

@router.post('/payments/{invoice_id}/mark-paid')
def paid(invoice_id:str,body:PaymentIn,authorization:str|None=Header(None)):
    require_permission('midas.payments.write',authorization)
    if body.amount is None: raise HTTPException(422,'payment_amount_required')
    try:return mark_paid(invoice_id,body.amount,body.external_payment_ref)
    except ValueError as e: raise HTTPException(409,str(e))
