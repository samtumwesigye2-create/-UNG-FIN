from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime, timezone
from uuid import uuid4
import json, os, urllib.error, urllib.request

router=APIRouter(prefix='/v1/nexus',tags=['NEXUS Integration'])
JANUS_BASE_URL=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')
_events=[]

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
    req=urllib.request.Request(JANUS_BASE_URL+'/v1/auth/introspect',data=b'',method='POST',headers={'Authorization':authorization,'User-Agent':'UNG-MIDAS/0.3.0'})
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
    mid=b.message_id or str(uuid4())
    for e in _events:
        if e['message_id']==mid:return {'accepted':True,'duplicate':True,'event':e}
    status='accepted'
    if b.message_type=='PROCURE.PURCHASE_ORDER.AWARDED': status='finance_commitment_received'
    event={'message_id':mid,'source_system':b.source_system,'target_system':b.target_system,'message_type':b.message_type,'payload':b.payload,'status':status,'principal_id':principal.get('id'),'created_at':datetime.now(timezone.utc).isoformat()}
    _events.append(event)
    return {'accepted':True,'duplicate':False,'event':event}

@router.get('/status')
def status():
    return {'status':'ready','service':'UNG-MIDAS','inbound':'/v1/nexus/inbound','events':len(_events),'last_event':_events[-1] if _events else None}
