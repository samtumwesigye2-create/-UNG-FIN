from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel
from domain import post_entry,list_entries
from integration import dependencies
SYSTEM_ID="UNG-MIDAS"; LEGACY_ID="UNG-FIN"; VERSION="0.3.0"
app=FastAPI(title=SYSTEM_ID,version=VERSION,description="UNG Finance System")
class EntryIn(BaseModel): account:str; amount:float; currency:str="USD"
def auth(p,h):
 s={x.strip() for x in (h or "").split(",") if x.strip()}
 if p not in s and "ung.admin" not in s: raise HTTPException(403,"UNG-JANUS permission required")
@app.get("/")
def root(): return {"system":SYSTEM_ID,"legacy_id":LEGACY_ID,"status":"online","version":VERSION,"nexus":"/v1/nexus/status"}
@app.get("/health")
def health(): return {"status":"ok","service":SYSTEM_ID,"version":VERSION}
@app.get("/ready")
def ready(): return {"status":"ready","service":SYSTEM_ID,"dependencies":dependencies()}
@app.get("/v1/system")
def system(): return {"system_id":SYSTEM_ID,"legacy_id":LEGACY_ID,"domain":"finance","dependencies":dependencies(),"capabilities":["ledger","nexus-procurement-handoff"]}
@app.get("/v1/ledger")
def ledger(x_ung_permissions:str|None=Header(None)): auth("midas.ledger.read",x_ung_permissions); return list_entries()
@app.post("/v1/ledger",status_code=201)
def post(body:EntryIn,x_ung_permissions:str|None=Header(None)): auth("midas.ledger.post",x_ung_permissions); return post_entry(body.account,body.amount,body.currency)
from nexus_bridge import router as nexus_router
app.include_router(nexus_router)
