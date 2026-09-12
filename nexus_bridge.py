from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Any
from uuid import uuid4
from sqlalchemy import text
from storage import transaction
from midas_events import process_inbound_event
from midas_auth import require_permission

router=APIRouter(prefix='/v1/nexus',tags=['NEXUS Integration'])
class Envelope(BaseModel):
    message_id:str|None=None
    source_system:str
    target_system:str
    message_type:str
    payload:dict[str,Any]=Field(default_factory=dict)
    sent_at:str|None=None
    principal_id:str|None=None

@router.post('/inbound',status_code=202)
def inbound(b:Envelope,authorization:str|None=Header(None)):
    principal=require_permission('nexus.messages.write',authorization)
    if b.target_system!='UNG-MIDAS': raise HTTPException(409,'wrong_target_system')
    payload=b.model_dump(); payload['message_id']=b.message_id or str(uuid4()); payload['principal_id']=principal.get('id')
    try:return process_inbound_event(payload)
    except ValueError as e: raise HTTPException(409,str(e))

@router.get('/status')
def status():
    try:
        with transaction() as c:
            count=c.execute(text('SELECT COUNT(*) n FROM midas_inbound_events')).mappings().first()['n']
            last=c.execute(text('SELECT * FROM midas_inbound_events ORDER BY processed_at DESC LIMIT 1')).mappings().first()
        return {'status':'ready','service':'UNG-MIDAS','inbound':'/v1/nexus/inbound','events':count,'last_event':dict(last) if last else None}
    except Exception:
        return {'status':'degraded','service':'UNG-MIDAS','inbound':'/v1/nexus/inbound','events':0,'last_event':None}
