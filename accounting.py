from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from midas_auth import require_permission
from sqlalchemy import text
from storage import transaction

router=APIRouter(prefix='/v1/accounting',tags=['accounting'])

def _now(): return datetime.now(timezone.utc)
def _d(value): return Decimal(str(value or 0))

def validate_balanced(lines)->bool:
    if not lines:return False
    debit=sum((_d(x.get('debit')) for x in lines),Decimal('0'))
    credit=sum((_d(x.get('credit')) for x in lines),Decimal('0'))
    return debit==credit

def validate_document_lines(lines):
    if not lines: raise ValueError('accounting_document_requires_lines')
    for line in lines:
        debit,credit=_d(line.get('debit')), _d(line.get('credit'))
        if debit<0 or credit<0: raise ValueError('accounting_amount_cannot_be_negative')
        if debit>0 and credit>0: raise ValueError('line_cannot_have_both_debit_and_credit')
        if debit==0 and credit==0: raise ValueError('line_requires_debit_or_credit')
    if not validate_balanced(lines): raise ValueError('accounting_document_unbalanced')
    return True

def assert_posting_period_open(posting_date):
    with transaction() as c:
        periods_table=c.execute(text("SELECT to_regclass('public.midas_accounting_periods')")).scalar()
        if not periods_table:return True
        row=c.execute(text('''SELECT status FROM midas_accounting_periods
          WHERE :d BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1'''),{'d':posting_date}).mappings().first()
        if row and row['status']!='open': raise ValueError('accounting_period_closed')
    return True

def init_accounting():
    with transaction() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_accounting_documents(
          id UUID PRIMARY KEY, document_type TEXT NOT NULL, reference TEXT NOT NULL,
          source_system TEXT NOT NULL, source_event_id TEXT NULL, posting_date DATE NOT NULL,
          currency TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('posted','reversed')),
          reverses_document_id UUID NULL REFERENCES midas_accounting_documents(id), created_at TIMESTAMPTZ NOT NULL)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_accounting_lines(
          id UUID PRIMARY KEY, document_id UUID NOT NULL REFERENCES midas_accounting_documents(id),
          line_no INTEGER NOT NULL, account_code TEXT NOT NULL,
          debit NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(debit>=0),
          credit NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(credit>=0), vendor_id TEXT NULL,
          cost_center_ref TEXT NULL, tax_code TEXT NULL, reference TEXT NULL,
          UNIQUE(document_id,line_no))'''))
        c.execute(text("""CREATE OR REPLACE FUNCTION midas_accounting_lines_immutable() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'midas_accounting_lines_are_append_only'; END; $$ LANGUAGE plpgsql"""))
        c.execute(text('DROP TRIGGER IF EXISTS trg_midas_accounting_lines_immutable ON midas_accounting_lines'))
        c.execute(text('''CREATE TRIGGER trg_midas_accounting_lines_immutable BEFORE UPDATE OR DELETE ON midas_accounting_lines
          FOR EACH ROW EXECUTE FUNCTION midas_accounting_lines_immutable()'''))
        c.execute(text("""CREATE OR REPLACE FUNCTION midas_accounting_document_guard() RETURNS trigger AS $$
          BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'midas_accounting_documents_are_append_only'; END IF;
            IF OLD.status='posted' AND NEW.status='reversed' AND OLD.id=NEW.id AND OLD.reference=NEW.reference
               AND OLD.document_type=NEW.document_type AND OLD.source_system=NEW.source_system
               AND OLD.posting_date=NEW.posting_date AND OLD.currency=NEW.currency THEN RETURN NEW; END IF;
            RAISE EXCEPTION 'midas_accounting_documents_are_append_only';
          END; $$ LANGUAGE plpgsql"""))
        c.execute(text('DROP TRIGGER IF EXISTS trg_midas_accounting_document_guard ON midas_accounting_documents'))
        c.execute(text('''CREATE TRIGGER trg_midas_accounting_document_guard BEFORE UPDATE OR DELETE ON midas_accounting_documents
          FOR EACH ROW EXECUTE FUNCTION midas_accounting_document_guard()'''))

def post_document(document_type, reference, source_system, source_event_id, posting_date, currency, lines):
    validate_document_lines(lines)
    document_id=str(uuid4()); created_at=_now(); posting_date=posting_date or date.today()
    assert_posting_period_open(posting_date)
    with transaction() as c:
        c.execute(text('''INSERT INTO midas_accounting_documents
          (id,document_type,reference,source_system,source_event_id,posting_date,currency,status,reverses_document_id,created_at)
          VALUES(:id,:dt,:ref,:ss,:seid,:pd,:cur,'posted',NULL,:ca)'''),
          {'id':document_id,'dt':document_type,'ref':reference,'ss':source_system,'seid':source_event_id,'pd':posting_date,'cur':currency,'ca':created_at})
        for idx,line in enumerate(lines,1):
            c.execute(text('''INSERT INTO midas_accounting_lines
              (id,document_id,line_no,account_code,debit,credit,vendor_id,cost_center_ref,tax_code,reference)
              VALUES(:id,:did,:ln,:acct,:debit,:credit,:vendor,:cc,:tax,:ref)'''),
              {'id':str(uuid4()),'did':document_id,'ln':line.get('line_no',idx),'acct':line['account_code'],
               'debit':_d(line.get('debit')),'credit':_d(line.get('credit')),'vendor':line.get('vendor_id'),
               'cc':line.get('cost_center_ref'),'tax':line.get('tax_code'),'ref':line.get('reference')})
    return get_document(document_id)

def get_document(document_id):
    with transaction() as c:
        header=c.execute(text('SELECT * FROM midas_accounting_documents WHERE id=:id'),{'id':document_id}).mappings().first()
        if not header:return None
        lines=c.execute(text('SELECT * FROM midas_accounting_lines WHERE document_id=:id ORDER BY line_no'),{'id':document_id}).mappings().all()
        return {'document':dict(header),'lines':[dict(x) for x in lines]}

def reverse_document(document_id, source_event_id=None):
    original=get_document(document_id)
    if not original: raise ValueError('accounting_document_not_found')
    if original['document']['status']!='posted': raise ValueError('accounting_document_not_posted')
    reverse_lines=[{**dict(line),'debit':_d(line['credit']),'credit':_d(line['debit'])} for line in original['lines']]
    reversal_id=str(uuid4()); h=original['document']; reversal_date=date.today()
    validate_document_lines(reverse_lines); assert_posting_period_open(reversal_date)
    with transaction() as c:
        c.execute(text('''INSERT INTO midas_accounting_documents
          (id,document_type,reference,source_system,source_event_id,posting_date,currency,status,reverses_document_id,created_at)
          VALUES(:id,'reversal',:ref,'UNG-MIDAS',:seid,:pd,:cur,'posted',:orig,:ca)'''),
          {'id':reversal_id,'ref':f"REV-{h['reference']}",'seid':source_event_id,'pd':reversal_date,'cur':h['currency'],'orig':document_id,'ca':_now()})
        for idx,line in enumerate(reverse_lines,1):
            c.execute(text('''INSERT INTO midas_accounting_lines
              (id,document_id,line_no,account_code,debit,credit,vendor_id,cost_center_ref,tax_code,reference)
              VALUES(:id,:did,:ln,:acct,:debit,:credit,:vendor,:cc,:tax,:ref)'''),
              {'id':str(uuid4()),'did':reversal_id,'ln':idx,'acct':line['account_code'],'debit':line['debit'],'credit':line['credit'],
               'vendor':line.get('vendor_id'),'cc':line.get('cost_center_ref'),'tax':line.get('tax_code'),'ref':line.get('reference')})
        c.execute(text("UPDATE midas_accounting_documents SET status='reversed' WHERE id=:id"),{'id':document_id})
    return get_document(reversal_id)

@router.get('/documents')
def list_documents(authorization:str|None=Header(None)):
    require_permission('midas.accounting.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text('SELECT * FROM midas_accounting_documents ORDER BY created_at DESC')).mappings().all()]

@router.get('/documents/{document_id}')
def document(document_id:str,authorization:str|None=Header(None)):
    require_permission('midas.accounting.read',authorization); result=get_document(document_id)
    if not result: raise HTTPException(404,'accounting_document_not_found')
    return result

@router.get('/trial-balance')
def trial_balance(authorization:str|None=Header(None)):
    require_permission('midas.accounting.read',authorization)
    with transaction() as c:
        rows=c.execute(text('''SELECT l.account_code,SUM(l.debit) debit,SUM(l.credit) credit,SUM(l.debit-l.credit) balance
          FROM midas_accounting_lines l JOIN midas_accounting_documents d ON d.id=l.document_id
          GROUP BY l.account_code ORDER BY l.account_code''')).mappings().all()
        return [dict(x) for x in rows]
