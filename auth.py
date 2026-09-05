import os,json,urllib.request
from fastapi import Header,HTTPException
JANUS=os.getenv('JANUS_BASE_URL',os.getenv('IAM_BASE_URL',''))
def principal(authorization:str|None=Header(None)):
 if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'Bearer token required')
 if not JANUS:raise HTTPException(503,'UNG-JANUS not configured')
 try:
  req=urllib.request.Request(JANUS.rstrip('/')+'/v1/principal',headers={'Authorization':authorization});return json.loads(urllib.request.urlopen(req,timeout=3).read())
 except Exception:raise HTTPException(401,'Invalid UNG-JANUS session')
