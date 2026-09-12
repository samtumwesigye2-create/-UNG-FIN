from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from midas_auth import require_permission
from storage import transaction

router=APIRouter(prefix='/v1/treasury',tags=['treasury'])

def _now(): return datetime.now(timezone.utc)

class BankAccountIn(BaseModel):
    account_name:str
    bank_name:str
    account_number_masked:str
    currency:str='USD'
    gl_account_code:str='100000-CASH'

class StatementLineIn(BaseModel):
    bank_account_id:str
    statement_ref:str
    transaction_date:date
    amount:Decimal
    description:str=''
    external_ref:str|None=None

class PeriodIn(BaseModel):
    period_code:str
    start_date:date
    end_date:date

def init_treasury():
    with transaction() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_bank_accounts(
          id UUID PRIMARY KEY, account_name TEXT NOT NULL, bank_name TEXT NOT NULL,
          account_number_masked TEXT NOT NULL, currency TEXT NOT NULL, gl_account_code TEXT NOT NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_bank_statement_lines(
          id UUID PRIMARY KEY, bank_account_id UUID NOT NULL REFERENCES midas_bank_accounts(id),
          statement_ref TEXT NOT NULL, transaction_date DATE NOT NULL, amount NUMERIC(18,4) NOT NULL,
          description TEXT NOT NULL DEFAULT '', external_ref TEXT NULL,
          reconciliation_status TEXT NOT NULL DEFAULT 'unmatched' CHECK(reconciliation_status IN ('unmatched','matched','exception')),
          accounting_document_id UUID NULL REFERENCES midas_accounting_documents(id), created_at TIMESTAMPTZ NOT NULL,
          UNIQUE(bank_account_id,statement_ref,external_ref))'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS midas_accounting_periods(
          period_code TEXT PRIMARY KEY, start_date DATE NOT NULL, end_date DATE NOT NULL,
          status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
          closed_at TIMESTAMPTZ NULL, closed_by TEXT NULL, CHECK(start_date<=end_date))'''))

def posting_period_is_open(posting_date):
    with transaction() as c:
        row=c.execute(text('''SELECT status FROM midas_accounting_periods
          WHERE :d BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1'''),{'d':posting_date}).mappings().first()
        return True if row is None else row['status']=='open'

def reconcile_statement_line(statement_line_id,accounting_document_id):
    with transaction() as c:
        line=c.execute(text('SELECT * FROM midas_bank_statement_lines WHERE id=:id FOR UPDATE'),{'id':statement_line_id}).mappings().first()
        if not line: raise ValueError('statement_line_not_found')
        if line['reconciliation_status']=='matched': raise ValueError('statement_line_already_matched')
        doc=c.execute(text('SELECT * FROM midas_accounting_documents WHERE id=:id'),{'id':accounting_document_id}).mappings().first()
        if not doc: raise ValueError('accounting_document_not_found')
        c.execute(text("UPDATE midas_bank_statement_lines SET reconciliation_status='matched',accounting_document_id=:doc WHERE id=:id"),{'doc':accounting_document_id,'id':statement_line_id})
    return {'statement_line_id':statement_line_id,'accounting_document_id':accounting_document_id,'status':'matched'}

@router.post('/bank-accounts',status_code=201)
def create_bank_account(body:BankAccountIn,authorization:str|None=Header(None)):
    require_permission('midas.treasury.write',authorization); iid=str(uuid4())
    with transaction() as c:
        row=c.execute(text('''INSERT INTO midas_bank_accounts(id,account_name,bank_name,account_number_masked,currency,gl_account_code,created_at)
          VALUES(:id,:name,:bank,:num,:cur,:gl,:now) RETURNING *'''),{'id':iid,'name':body.account_name,'bank':body.bank_name,'num':body.account_number_masked,'cur':body.currency.upper(),'gl':body.gl_account_code,'now':_now()}).mappings().first()
        return dict(row)

@router.post('/statement-lines',status_code=201)
def create_statement_line(body:StatementLineIn,authorization:str|None=Header(None)):
    require_permission('midas.treasury.write',authorization)
    with transaction() as c:
        row=c.execute(text('''INSERT INTO midas_bank_statement_lines(id,bank_account_id,statement_ref,transaction_date,amount,description,external_ref,created_at)
          VALUES(:id,:bank,:ref,:d,:amt,:desc,:ext,:now) RETURNING *'''),{'id':str(uuid4()),'bank':body.bank_account_id,'ref':body.statement_ref,'d':body.transaction_date,'amt':body.amount,'desc':body.description,'ext':body.external_ref,'now':_now()}).mappings().first(); return dict(row)

@router.post('/statement-lines/{line_id}/reconcile/{document_id}')
def reconcile(line_id:str,document_id:str,authorization:str|None=Header(None)):
    require_permission('midas.treasury.reconcile',authorization)
    try:return reconcile_statement_line(line_id,document_id)
    except ValueError as e: raise HTTPException(409,str(e))

@router.get('/reconciliation-summary')
def reconciliation_summary(authorization:str|None=Header(None)):
    require_permission('midas.treasury.read',authorization)
    with transaction() as c:
        rows=c.execute(text('''SELECT reconciliation_status,COUNT(*) count,COALESCE(SUM(amount),0) amount
          FROM midas_bank_statement_lines GROUP BY reconciliation_status ORDER BY reconciliation_status''')).mappings().all(); return [dict(x) for x in rows]

@router.post('/periods',status_code=201)
def create_period(body:PeriodIn,authorization:str|None=Header(None)):
    require_permission('midas.close.manage',authorization)
    with transaction() as c:
        row=c.execute(text('''INSERT INTO midas_accounting_periods(period_code,start_date,end_date,status)
          VALUES(:code,:s,:e,'open') RETURNING *'''),{'code':body.period_code,'s':body.start_date,'e':body.end_date}).mappings().first(); return dict(row)

@router.post('/periods/{period_code}/close')
def close_period(period_code:str,x_ung_actor:str|None=Header(None),authorization:str|None=Header(None)):
    require_permission('midas.close.manage',authorization)
    with transaction() as c:
        row=c.execute(text("UPDATE midas_accounting_periods SET status='closed',closed_at=:now,closed_by=:actor WHERE period_code=:code AND status='open' RETURNING *"),{'now':_now(),'actor':x_ung_actor or 'unknown','code':period_code}).mappings().first()
        if not row: raise HTTPException(409,'period_not_open_or_not_found')
        return dict(row)

@router.get('/periods')
def periods(authorization:str|None=Header(None)):
    require_permission('midas.close.read',authorization)
    with transaction() as c:return [dict(x) for x in c.execute(text('SELECT * FROM midas_accounting_periods ORDER BY start_date DESC')).mappings().all()]
