import json, os, urllib.error, urllib.request
from fastapi import HTTPException

JANUS_BASE_URL=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')

def permission_allowed(principal, permission):
    perms=set((principal or {}).get('permissions') or [])
    return permission in perms or 'ung.admin' in perms

def require_permission(permission, authorization):
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401,'JANUS bearer token required')
    req=urllib.request.Request(
        JANUS_BASE_URL+'/v1/auth/introspect',
        data=b'',
        method='POST',
        headers={'Authorization':authorization,'User-Agent':'UNG-MIDAS/0.4.0'},
    )
    try:
        with urllib.request.urlopen(req,timeout=5) as r:
            data=json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise HTTPException(401,'JANUS token invalid or expired')
        raise HTTPException(503,'JANUS authorization unavailable')
    except Exception:
        raise HTTPException(503,'JANUS authorization unavailable')
    principal=data.get('principal') or {}
    if not permission_allowed(principal,permission):
        raise HTTPException(403,f'Missing JANUS permission: {permission}')
    return principal
