import os
JANUS_BASE_URL=os.getenv("JANUS_BASE_URL",os.getenv("IAM_BASE_URL","")); ATLAS_BASE_URL=os.getenv("ATLAS_BASE_URL",""); PULSAR_BASE_URL=os.getenv("PULSAR_BASE_URL","")
def dependencies(): return {"identity":{"system":"UNG-JANUS","configured":bool(JANUS_BASE_URL)},"control_plane":{"system":"UNG-ATLAS","configured":bool(ATLAS_BASE_URL)},"event_relay":{"system":"UNG-PULSAR","configured":bool(PULSAR_BASE_URL)}}
