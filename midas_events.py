import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import text
from storage import transaction
from accounting import post_document, get_document
from ap import create_ap_item, get_invoice, cancel_invoice

def _now(): return datetime.now(timezone.utc)
def _d(v): return Decimal(str(v or 0))
def inbound_event_key(source_system,source_event_id): return f'{source_system}:{source_event_id}'

def init_midas_events():
    with transaction() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_inbound_events(source_system TEXT NOT NULL,source_event_id TEXT NOT NULL,message_type TEXT NOT NULL,payload JSONB NOT NULL,status TEXT NOT NULL,processed_at TIMESTAMPTZ NOT NULL,PRIMARY KEY(source_system,source_event_id))'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_procurement_commitments(order_id TEXT PRIMARY KEY,vendor_id TEXT NULL,amount NUMERIC(18,4) NOT NULL DEFAULT 0,currency TEXT NOT NULL,status TEXT NOT NULL,source_event_id TEXT NULL,updated_at TIMESTAMPTZ NOT NULL)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_event_outbox(id UUID PRIMARY KEY,idempotency_key TEXT UNIQUE NOT NULL,target_system TEXT NOT NULL,message_type TEXT NOT NULL,payload JSONB NOT NULL,delivery_status TEXT NOT NULL DEFAULT 'pending',attempt_count INTEGER NOT NULL DEFAULT 0,created_at TIMESTAMPTZ NOT NULL,delivered_at TIMESTAMPTZ NULL)'''))

def enqueue_outbox(idempotency_key,target_system,message_type,payload):
    with transaction() as c:
        row=c.execute(text('''INSERT INTO midas_event_outbox(id,idempotency_key,target_system,message_type,payload,delivery_status,attempt_count,created_at)
          VALUES(:id,:key,:target,:type,CAST(:payload AS JSONB),'pending',0,:now)
          ON CONFLICT(idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key RETURNING *'''),{'id':str(uuid4()),'key':idempotency_key,'target':target_system,'type':message_type,'payload':json.dumps(payload),'now':_now()}).mappings().first(); return dict(row)

def mark_outbox_delivered(outbox_id):
    with transaction() as c:
        row=c.execute(text("UPDATE midas_event_outbox SET delivery_status='delivered',attempt_count=attempt_count+1,delivered_at=:now WHERE id=:id RETURNING *"),{'id':outbox_id,'now':_now()}).mappings().first(); return dict(row) if row else None

def build_invoice_journal_lines(invoice,invoice_lines):
    lines=[]; line_no=1
    for item in invoice_lines:
        subtotal=_d(item['line_subtotal']); tax=_d(item.get('tax_amount')); withholding=_d(item.get('withholding_amount'))
        if subtotal:
            lines.append({'line_no':line_no,'account_code':item.get('expense_or_inventory_account') or '500000','debit':subtotal,'credit':Decimal('0'),'vendor_id':invoice['vendor_id'],'cost_center_ref':item.get('cost_center_ref'),'tax_code':None,'reference':invoice.get('invoice_ref')}); line_no+=1
        if tax:
            lines.append({'line_no':line_no,'account_code':'140000-TAX-INPUT','debit':tax,'credit':Decimal('0'),'vendor_id':invoice['vendor_id'],'cost_center_ref':item.get('cost_center_ref'),'tax_code':item.get('tax_code'),'reference':invoice.get('invoice_ref')}); line_no+=1
        if withholding:
            lines.append({'line_no':line_no,'account_code':'210500-WITHHOLDING','debit':Decimal('0'),'credit':withholding,'vendor_id':invoice['vendor_id'],'cost_center_ref':item.get('cost_center_ref'),'tax_code':item.get('withholding_code'),'reference':invoice.get('invoice_ref')}); line_no+=1
    lines.append({'line_no':line_no,'account_code':'200000-ACCOUNTS-PAYABLE','debit':Decimal('0'),'credit':_d(invoice['total_amount']),'vendor_id':invoice['vendor_id'],'cost_center_ref':None,'tax_code':None,'reference':invoice.get('invoice_ref')})
    return lines

def _record_inbound(source,event_id,mtype,payload,status):
    with transaction() as c:c.execute(text('''INSERT INTO midas_inbound_events(source_system,source_event_id,message_type,payload,status,processed_at) VALUES(:source,:id,:type,CAST(:payload AS JSONB),:status,:now) ON CONFLICT(source_system,source_event_id) DO NOTHING'''),{'source':source,'id':event_id,'type':mtype,'payload':json.dumps(payload),'status':status,'now':_now()})
def _existing_inbound(source,event_id):
    with transaction() as c:
        row=c.execute(text('SELECT * FROM midas_inbound_events WHERE source_system=:s AND source_event_id=:id'),{'s':source,'id':event_id}).mappings().first(); return dict(row) if row else None

def _invoice_by_payload(payload):
    invoice_id=payload.get('invoice_id') or payload.get('midas_invoice_id')
    if invoice_id:return get_invoice(str(invoice_id))
    ref=payload.get('invoice_ref'); vendor=payload.get('vendor_id')
    if ref and vendor:
        with transaction() as c:
            row=c.execute(text('SELECT id FROM midas_supplier_invoices WHERE vendor_id=:v AND invoice_ref=:r'),{'v':vendor,'r':ref}).mappings().first(); return get_invoice(str(row['id'])) if row else None
    return None

def _existing_document_for_event(event_id):
    with transaction() as c:
        row=c.execute(text("SELECT id FROM midas_accounting_documents WHERE source_system='UNG-PROCURE' AND source_event_id=:id ORDER BY created_at LIMIT 1"),{'id':event_id}).mappings().first(); return get_document(str(row['id'])) if row else None

def process_inbound_event(envelope):
    if hasattr(envelope,'model_dump'): envelope=envelope.model_dump()
    source=envelope.get('source_system') or 'UNKNOWN'; event_id=envelope.get('message_id') or envelope.get('source_event_id')
    if not event_id: raise ValueError('source_event_id_required')
    mtype=envelope['message_type']; payload=envelope.get('payload') or {}
    existing=_existing_inbound(source,event_id)
    if existing:return {'accepted':True,'duplicate':True,'status':existing['status'],'event':existing}
    if mtype=='PROCURE.PURCHASE_ORDER.AWARDED':
        order_id=str(payload.get('order_id') or '')
        if not order_id: raise ValueError('order_id_required')
        with transaction() as c:c.execute(text('''INSERT INTO midas_procurement_commitments(order_id,vendor_id,amount,currency,status,source_event_id,updated_at) VALUES(:order,:vendor,:amount,:currency,:status,:event,:now) ON CONFLICT(order_id) DO UPDATE SET vendor_id=EXCLUDED.vendor_id,amount=EXCLUDED.amount,currency=EXCLUDED.currency,status=EXCLUDED.status,source_event_id=EXCLUDED.source_event_id,updated_at=EXCLUDED.updated_at'''),{'order':order_id,'vendor':payload.get('vendor_id'),'amount':_d(payload.get('amount')),'currency':payload.get('currency','USD'),'status':payload.get('status','awarded'),'event':event_id,'now':_now()}); status='finance_commitment_received'
    elif mtype=='PROCURE.MATCH.BLOCKED':
        found=_invoice_by_payload(payload)
        if not found: raise ValueError('invoice_not_found')
        with transaction() as c:c.execute(text("UPDATE midas_supplier_invoices SET match_status='blocked',payment_status='blocked',updated_at=:now WHERE id=:id AND accounting_status='draft'"),{'id':str(found['invoice']['id']),'now':_now()}); status='match_blocked'
    elif mtype=='PROCURE.MATCH.PASSED':
        found=_invoice_by_payload(payload)
        if not found: raise ValueError('invoice_not_found')
        inv=found['invoice']; iid=str(inv['id']); document=_existing_document_for_event(event_id)
        if not document: document=post_document('supplier_invoice',inv['invoice_ref'],'UNG-PROCURE',event_id,inv['posting_date'],inv['currency'],build_invoice_journal_lines(inv,found['lines']))
        doc_id=str(document['document']['id']); create_ap_item(iid,doc_id); match_status='matched_with_tolerance' if payload.get('match_status')=='matched_with_tolerance' else 'matched'
        with transaction() as c:c.execute(text("UPDATE midas_supplier_invoices SET match_status=:ms,accounting_status='posted',payment_status='eligible',accounting_document_id=:doc,updated_at=:now WHERE id=:id"),{'ms':match_status,'doc':doc_id,'now':_now(),'id':iid})
        enqueue_outbox(f'accounting-posted:{iid}','UNG-PROCURE','MIDAS.ACCOUNTING.POSTED',{'invoice_id':iid,'accounting_document_id':doc_id,'status':'posted'}); enqueue_outbox(f'payment-eligible:{iid}','UNG-PROCURE','MIDAS.PAYMENT.STATUS',{'invoice_id':iid,'payment_status':'eligible'}); status='accounting_posted'
    elif mtype in {'PROCURE.SUPPLIER_INVOICE.CANCELLED','MIDAS.SUPPLIER_INVOICE.CANCELLED'}:
        found=_invoice_by_payload(payload)
        if not found: raise ValueError('invoice_not_found')
        cancel_invoice(str(found['invoice']['id']),event_id); status='invoice_reversed'
    else: status='accepted'
    _record_inbound(source,event_id,mtype,payload,status)
    return {'accepted':True,'duplicate':False,'status':status,'event_id':event_id}
