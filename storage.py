import os,json
from sqlalchemy import create_engine,text
DB=os.getenv('DATABASE_URL','').replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1); E=create_engine(DB,pool_pre_ping=True) if DB else None
def init_db():
 if E:
  with E.begin() as c:c.execute(text('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload JSONB NOT NULL)'));c.execute(text('CREATE TABLE IF NOT EXISTS outbox(id BIGSERIAL PRIMARY KEY,payload JSONB NOT NULL,delivered BOOLEAN NOT NULL DEFAULT FALSE)'))
def put(r):
 if E:
  with E.begin() as c:c.execute(text('INSERT INTO records(id,payload) VALUES(:id,CAST(:p AS JSONB)) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload'),{'id':r['id'],'p':json.dumps(r)})
 return r
def all_records():
 if not E:return []
 with E.begin() as c:return [x[0] for x in c.execute(text('SELECT payload FROM records ORDER BY id'))]
def enqueue(e):
 if E:
  with E.begin() as c:c.execute(text('INSERT INTO outbox(payload) VALUES(CAST(:p AS JSONB))'),{'p':json.dumps(e)})
