from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text
from storage import transaction

router=APIRouter(prefix='/v1/reports',tags=['financial-reporting'])

def _auth(p,h):
    s={x.strip() for x in (h or '').split(',') if x.strip()}
    if p not in s and 'ung.admin' not in s: raise HTTPException(403,'UNG-JANUS permission required')

def _ledger_rows():
    with transaction() as c:
        return [dict(x) for x in c.execute(text('''SELECT l.account_code,
          COALESCE(SUM(l.debit),0) debit,COALESCE(SUM(l.credit),0) credit,
          COALESCE(SUM(l.debit-l.credit),0) balance
          FROM midas_accounting_lines l JOIN midas_accounting_documents d ON d.id=l.document_id
          GROUP BY l.account_code ORDER BY l.account_code''')).mappings().all()]

def classify(account_code):
    code=str(account_code)
    if code.startswith('1'): return 'asset'
    if code.startswith('2'): return 'liability'
    if code.startswith('3'): return 'equity'
    if code.startswith('4'): return 'revenue'
    if code.startswith(('5','6','7','8','9')): return 'expense'
    return 'other'

@router.get('/trial-balance')
def trial_balance(x_ung_permissions:str|None=Header(None)):
    _auth('midas.reports.read',x_ung_permissions); rows=_ledger_rows()
    return {'rows':rows,'total_debit':sum(r['debit'] for r in rows),'total_credit':sum(r['credit'] for r in rows)}

@router.get('/profit-loss')
def profit_loss(x_ung_permissions:str|None=Header(None)):
    _auth('midas.reports.read',x_ung_permissions); rows=_ledger_rows()
    revenue=[]; expenses=[]
    for r in rows:
        t=classify(r['account_code'])
        if t=='revenue': revenue.append({**r,'amount':r['credit']-r['debit']})
        elif t=='expense': expenses.append({**r,'amount':r['debit']-r['credit']})
    total_revenue=sum(r['amount'] for r in revenue); total_expenses=sum(r['amount'] for r in expenses)
    return {'revenue':revenue,'expenses':expenses,'total_revenue':total_revenue,'total_expenses':total_expenses,'net_income':total_revenue-total_expenses}

@router.get('/balance-sheet')
def balance_sheet(x_ung_permissions:str|None=Header(None)):
    _auth('midas.reports.read',x_ung_permissions); rows=_ledger_rows(); sections={'assets':[],'liabilities':[],'equity':[]}
    for r in rows:
        t=classify(r['account_code'])
        if t=='asset': sections['assets'].append({**r,'amount':r['debit']-r['credit']})
        elif t=='liability': sections['liabilities'].append({**r,'amount':r['credit']-r['debit']})
        elif t=='equity': sections['equity'].append({**r,'amount':r['credit']-r['debit']})
    sections['totals']={'assets':sum(x['amount'] for x in sections['assets']),'liabilities':sum(x['amount'] for x in sections['liabilities']),'equity':sum(x['amount'] for x in sections['equity'])}
    return sections

@router.get('/cash-flow')
def cash_flow(x_ung_permissions:str|None=Header(None)):
    _auth('midas.reports.read',x_ung_permissions)
    with transaction() as c:
        rows=[dict(x) for x in c.execute(text("""SELECT d.document_type,d.reference,d.posting_date,l.account_code,l.debit,l.credit,
          (l.debit-l.credit) net_cash FROM midas_accounting_lines l JOIN midas_accounting_documents d ON d.id=l.document_id
          WHERE l.account_code LIKE '100%' ORDER BY d.posting_date,d.created_at""")).mappings().all()]
    return {'cash_movements':rows,'net_cash_change':sum(r['net_cash'] for r in rows)}

@router.get('/audit-summary')
def audit_summary(x_ung_permissions:str|None=Header(None)):
    _auth('midas.reports.audit',x_ung_permissions)
    with transaction() as c:
        docs=c.execute(text('SELECT COUNT(*) FROM midas_accounting_documents')).scalar_one()
        events=c.execute(text('SELECT COUNT(*) FROM midas_event_outbox')).scalar_one()
        inbound=c.execute(text('SELECT COUNT(*) FROM midas_inbound_events')).scalar_one()
    return {'accounting_documents':docs,'outbox_events':events,'inbound_events':inbound}
