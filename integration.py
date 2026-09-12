import os
JANUS_BASE_URL=os.getenv('JANUS_BASE_URL',os.getenv('IAM_BASE_URL',''))
ATLAS_BASE_URL=os.getenv('ATLAS_BASE_URL','')
PULSAR_BASE_URL=os.getenv('PULSAR_BASE_URL','')
NEXUS_BASE_URL=os.getenv('NEXUS_BASE_URL','')
VECTOR_BASE_URL=os.getenv('VECTOR_BASE_URL','')

def dependencies():
    return {
      'identity':{'system':'UNG-JANUS','configured':bool(JANUS_BASE_URL)},
      'control_plane':{'system':'UNG-ATLAS','configured':bool(ATLAS_BASE_URL)},
      'event_relay':{'system':'UNG-PULSAR','configured':bool(PULSAR_BASE_URL)},
      'integration_bus':{'system':'UNG-NEXUS','configured':bool(NEXUS_BASE_URL)},
      'inventory_handoff':{'system':'UNG-VECTOR','configured':bool(VECTOR_BASE_URL)}
    }
