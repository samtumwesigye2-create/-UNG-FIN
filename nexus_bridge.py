from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Any
from uuid import uuid4
import json, os, urllib.error, urllib.request
from sqlalchemy import text
from storage import transaction
from midas_events import process_inbound_event

router=APIRouter(prefix='/v1/nexus',tags=['NEXUS Integration'])
JANUS_BASE_URL=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')

class Envelope(BaseModel):
    message_id:str|None=None
    source_system:str
    target_system:str
    message_type:str
    payload:dict[str,Any]=Field(default_factory=dict)
    sent_at:str|None=None
    principal_id:str|None=None

def janus_auth(permission,authorization):
    if not authorization or not authorization.lower().startswith('bearer '): raise HTTPException(401,'JANUS bearer token required')
    req=urllib.request.Request(JANUS_BASE_URL+'/v1/auth/introspect',data=b'',method='POST',headers={'Authorization':authorization,'User-Agent':'UNG-MIDAS/0.4.0'})
    try:
        with urllib.request.urlopen(req,timeout=5) as r:data=json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise HTTPException(401,'JANUS token invalid or expired')
        raise HTTPException(503,'JANUS authorization unavailable')
    except Exception: raise HTTPException(503,'JANUS authorization unavailable')
    principal=data.get('principal') or {}; perms=set(principal.get('permissions') or [])
    if permission not in perms and 'ung.admin' not in perms and 'platform:service' not in perms: raise HTTPException(403,f'Missing JANUS permission: {permission}')
    return principal

@router.post('/inbound',status_code=202)
def inbound(b:Envelope,authorization:str|None=Header(None)):
    principal=janus_auth('nexus.messages.write',authorization)
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
