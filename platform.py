import os, json, urllib.request
from datetime import datetime, timezone
DATABASE_URL=os.getenv('DATABASE_URL',''); JANUS_BASE_URL=os.getenv('JANUS_BASE_URL',os.getenv('IAM_BASE_URL','')); PULSAR_BASE_URL=os.getenv('PULSAR_BASE_URL','')
def production_gates(): return {'database':bool(DATABASE_URL),'janus':JANUS_BASE_URL.startswith('https://'),'pulsar':PULSAR_BASE_URL.startswith('https://')}
def audit_event(action,actor='system',resource=None):
 e={'type':'ung.audit','system':'UNG-MIDAS','action':action,'actor':actor,'resource':resource,'timestamp':datetime.now(timezone.utc).isoformat()}
 if PULSAR_BASE_URL:
  try:
   req=urllib.request.Request(PULSAR_BASE_URL.rstrip('/')+'/events',data=json.dumps(e).encode(),headers={'Content-Type':'application/json'},method='POST'); urllib.request.urlopen(req,timeout=2).read()
  except Exception: pass
 return e
