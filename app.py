from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel
from domain import post_entry,list_entries
from integration import dependencies
from storage import init_db
from accounting import init_accounting
from ap import init_ap
from midas_events import init_midas_events
from treasury import init_treasury
SYSTEM_ID='UNG-MIDAS'; LEGACY_ID='UNG-FIN'; VERSION='0.5.0'
app=FastAPI(title=SYSTEM_ID,version=VERSION,description='UNG Finance System')
class EntryIn(BaseModel): account:str; amount:float; currency:str='USD'
def auth(p,h):
 s={x.strip() for x in (h or '').split(',') if x.strip()}
 if p not in s and 'ung.admin' not in s: raise HTTPException(403,'UNG-JANUS permission required')
@app.on_event('startup')
def startup():
 init_db(); init_accounting(); init_ap(); init_midas_events(); init_treasury()
@app.get('/')
def root(): return {'system':SYSTEM_ID,'legacy_id':LEGACY_ID,'status':'online','version':VERSION,'nexus':'/v1/nexus/status'}
@app.get('/health')
def health(): return {'status':'ok','service':SYSTEM_ID,'version':VERSION}
@app.get('/ready')
def ready(): return {'status':'ready','service':SYSTEM_ID,'dependencies':dependencies()}
@app.get('/v1/system')
def system(): return {'system_id':SYSTEM_ID,'legacy_id':LEGACY_ID,'domain':'finance','dependencies':dependencies(),'capabilities':['ledger','nexus-procurement-handoff','supplier-invoices','accounts-payable','vendor-open-items','vendor-cleared-items','double-entry-accounting','immutable-accounting-history','tax-withholding-accounting','procure-match-consumer','payment-eligibility','payment-status','persistent-outbox','idempotent-finance-events','bank-cash-management','bank-reconciliation','accounting-period-close','trial-balance','profit-loss','balance-sheet','cash-flow','audit-reporting']}
@app.get('/v1/ledger')
def ledger(x_ung_permissions:str|None=Header(None)): auth('midas.ledger.read',x_ung_permissions); return list_entries()
@app.post('/v1/ledger',status_code=201)
def post(body:EntryIn,x_ung_permissions:str|None=Header(None)): auth('midas.ledger.post',x_ung_permissions); return post_entry(body.account,body.amount,body.currency)

# Production uvicorn target is app:app, so every production router is mounted here.
from nexus_bridge import router as nexus_router
from accounting import router as accounting_router
from ap import router as ap_router
from treasury import router as treasury_router
from reporting import router as reporting_router
from finance_kpis import router as finance_kpis_router
app.include_router(nexus_router)
app.include_router(accounting_router)
app.include_router(ap_router)
app.include_router(treasury_router)
app.include_router(reporting_router)
app.include_router(finance_kpis_router)
