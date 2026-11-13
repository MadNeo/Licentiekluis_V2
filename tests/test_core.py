import os, sys
from pathlib import Path
from cryptography.fernet import Fernet
os.environ['DATABASE_URL']='sqlite:///' + str(Path('/tmp/licentiekluis-test.db'))
os.environ['LICENTIEKLUIS_KEY']=Fernet.generate_key().decode()
os.environ['COOKIE_SECURE']='false'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import app,Base,engine,Account,Person,ph
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

def test_flow():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=Person(name='Test Persoon',email='test@example.invalid',department='IT');s.add(p);s.flush()
        s.add_all([Account(username='admin',password_hash=ph.hash('een-lang-wachtwoord'),role='admin'),Account(username='user',password_hash=ph.hash('een-lang-wachtwoord'),role='user',person_id=p.id)]);s.commit()
    c=TestClient(app)
    r=c.post('/api/v1/login',json={'username':'admin','password':'een-lang-wachtwoord'});assert r.status_code==200
    h={'X-CSRF-Token':r.json()['csrf']}
    assert c.post('/api/v1/licenses',json={},headers={}).status_code==403
    r=c.post('/api/v1/licenses',headers=h,json={'reference':'A-1','name':'Office','vendor':'Microsoft','seats':1,'product_key':'fictieve-sleutel'}); assert r.status_code==200
    lid=r.json()['id']
    assert c.post('/api/v1/assignments',headers=h,json={'license_id':lid,'person_id':1}).status_code==200
    assert c.post('/api/v1/assignments',headers=h,json={'license_id':lid,'person_id':1}).status_code==409
    assert b'fictieve-sleutel' not in Path('/tmp/licentiekluis-test.db').read_bytes()
    assert c.post('/api/v1/logout',headers=h).status_code==200
    r=c.post('/api/v1/login',json={'username':'user','password':'een-lang-wachtwoord'}); h={'X-CSRF-Token':r.json()['csrf']}
    assert len(c.get('/api/v1/licenses').json())==1
    assert c.get('/api/v1/persons').status_code==403
    assert c.post(f'/api/v1/licenses/{lid}/key',headers=h,json={}).json()['key']=='fictieve-sleutel'
