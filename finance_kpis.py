from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
import os, psycopg
from psycopg.rows import dict_row
from app import auth

router=APIRouter(prefix='/v1/kpis',tags=['Supply Chain Finance KPIs'])
DB=os.getenv('DATABASE_URL','')
def conn():
    if not DB: raise HTTPException(503,'database_not_configured')
    return psycopg.connect(DB,row_factory=dict_row)
def now(): return datetime.now(timezone.utc)
def ensure_schema():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS midas_supply_chain_finance(id UUID PRIMARY KEY,entity_id TEXT NOT NULL,inventory_days DOUBLE PRECISION NULL,receivables_days DOUBLE PRECISION NULL,payables_days DOUBLE PRECISION NULL,total_supply_chain_cost DOUBLE PRECISION NULL,orders_count DOUBLE PRECISION NULL,currency TEXT NOT NULL DEFAULT 'USD',measured_at TIMESTAMPTZ NOT NULL)''')
class FinanceMetricIn(BaseModel):
    entity_id:str='enterprise';inventory_days:float|None=Field(default=None,ge=0);receivables_days:float|None=Field(default=None,ge=0);payables_days:float|None=Field(default=None,ge=0);total_supply_chain_cost:float|None=Field(default=None,ge=0);orders_count:float|None=Field(default=None,ge=0);currency:str='USD';measured_at:datetime|None=None
@router.post('/supply-chain-finance',status_code=201)
def record(b:FinanceMetricIn,x_ung_permissions:str|None=Header(None)):
    auth('midas.ledger.post',x_ung_permissions);ensure_schema()
    with conn() as c:return c.execute('INSERT INTO midas_supply_chain_finance VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.entity_id,b.inventory_days,b.receivables_days,b.payables_days,b.total_supply_chain_cost,b.orders_count,b.currency.upper(),b.measured_at or now())).fetchone()
@router.get('/supply-chain')
def snapshot(x_ung_permissions:str|None=Header(None)):
    auth('midas.ledger.read',x_ung_permissions);ensure_schema()
    with conn() as c:r=c.execute('SELECT * FROM midas_supply_chain_finance ORDER BY measured_at DESC LIMIT 1').fetchone()
    if not r:return {'source_system':'UNG-MIDAS','status':'no-data','observations':[],'generated_at':now()}
    obs=[]
    if r['inventory_days'] is not None and r['receivables_days'] is not None and r['payables_days'] is not None:
        obs.append({'kpi_key':'cash_to_cash_cycle_time','value':float(r['inventory_days'])+float(r['receivables_days'])-float(r['payables_days']),'entity_id':r['entity_id'],'source_system':'UNG-MIDAS'})
    if r['total_supply_chain_cost'] is not None:
        obs.append({'kpi_key':'total_supply_chain_cost','value':float(r['total_supply_chain_cost']),'entity_id':r['entity_id'],'source_system':'UNG-MIDAS'})
    if r['total_supply_chain_cost'] is not None and r['orders_count'] and float(r['orders_count'])>0:
        obs.append({'kpi_key':'logistics_cost_per_order','value':float(r['total_supply_chain_cost'])/float(r['orders_count']),'entity_id':r['entity_id'],'source_system':'UNG-MIDAS'})
    return {'source_system':'UNG-MIDAS','currency':r['currency'],'observations':obs,'measured_at':r['measured_at'],'generated_at':now()}
