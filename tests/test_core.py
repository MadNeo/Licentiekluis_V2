import os, sys, tempfile
from pathlib import Path
from cryptography.fernet import Fernet
os.environ['DATABASE_URL']='sqlite:///' + str(Path(tempfile.mkdtemp())/'test.db')
os.environ['LICENTIEKLUIS_KEY']=Fernet.generate_key().decode()
os.environ['COOKIE_SECURE']='false'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pyotp
import pytest
from app import app,Base,engine,Account,Person,ph
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

@pytest.fixture
def db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=Person(name='Test Persoon',email='test@example.invalid',department='IT'); s.add(p); s.flush()
        s.add_all([Account(username='admin',password_hash=ph.hash('een-lang-wachtwoord'),role='admin'),Account(username='user',password_hash=ph.hash('een-lang-wachtwoord'),role='user',person_id=p.id),Account(username='other',password_hash=ph.hash('een-lang-wachtwoord'),role='user')]);s.commit()
    return TestClient(app)
def login(c,name):
    r=c.post('/api/v1/login',json={'username':name,'password':'een-lang-wachtwoord'}); assert r.status_code==200; return r.json()
def enroll(c):
    me=login(c,'admin'); assert me['stage']=='enroll'
    assert c.get('/api/v1/licenses').status_code==403
    h={'X-CSRF-Token':me['csrf']}; r=c.post('/api/v1/mfa/setup',headers=h,json={}); assert r.status_code==200
    r=c.post('/api/v1/mfa/confirm',headers=h,json={'code':pyotp.TOTP(r.json()['secret']).now()}); assert r.status_code==200
    return r.json()
def headers(me): return {'X-CSRF-Token':me['csrf']}
def test_flow(db):
    c=db; me=enroll(c); h=headers(me)
    assert c.post('/api/v1/licenses',json={},headers={}).status_code==403
    r=c.post('/api/v1/licenses',headers=h,json={'reference':'A-1','name':'Office','vendor':'Microsoft','seats':1,'product_key':'fictieve-sleutel'}); assert r.status_code==200
    lid=r.json()['id']; assert c.post('/api/v1/assignments',headers=h,json={'license_id':lid,'person_id':1}).status_code==200
    assert c.post('/api/v1/assignments',headers=h,json={'license_id':lid,'person_id':1}).status_code==409
    assert b'fictieve-sleutel' not in Path(engine.url.database).read_bytes()
    assert c.post('/api/v1/logout',headers=h).status_code==200
    me=login(c,'user'); h=headers(me)
    assert len(c.get('/api/v1/licenses').json())==1
    assert c.get('/api/v1/accounts').status_code==403
    assert c.get('/api/v1/persons').status_code==403
    assert c.post(f'/api/v1/licenses/{lid}/key',headers=h,json={}).json()['key']=='fictieve-sleutel'
    c.post('/api/v1/logout',headers=h); me=login(c,'other')
    assert c.get('/api/v1/licenses').json()==[]
    assert c.post(f'/api/v1/licenses/{lid}/key',headers=headers(me),json={}).status_code==403

def test_recovery_code_and_invalidation(db):
    c=db; initial=enroll(c); code=initial['recovery_codes'][0]
    assert c.post('/api/v1/logout',headers=headers(initial)).status_code==200
    pending=login(c,'admin'); assert pending['stage']=='verify'
    r=c.post('/api/v1/mfa/verify',headers=headers(pending),json={'code':code}); assert r.status_code==200
    verified=r.json(); assert c.post('/api/v1/password',headers=headers(verified),json={'current_password':'een-lang-wachtwoord','new_password':'een-nieuw-lang-wachtwoord'}).status_code==200
    assert c.get('/api/v1/licenses').status_code==401
    r=c.post('/api/v1/login',json={'username':'admin','password':'een-nieuw-lang-wachtwoord'}); pending=r.json()
    assert c.post('/api/v1/mfa/verify',headers=headers(pending),json={'code':code}).status_code==401

def test_rate_limit(db):
    for _ in range(5): assert db.post('/api/v1/login',json={'username':'nobody','password':'wrong'}).status_code==401
    assert db.post('/api/v1/login',json={'username':'nobody','password':'wrong'}).status_code==429

def test_account_role_and_deactivation_revoke_session(db):
    admin=db; me=enroll(admin); h=headers(me)
    user=TestClient(app); user_me=login(user,'user'); assert user.get('/api/v1/licenses').status_code==200
    accounts=admin.get('/api/v1/accounts').json(); user_id=next(x['id'] for x in accounts if x['username']=='user')
    r=admin.put(f'/api/v1/accounts/{user_id}',headers=h,json={'role':'manager','active':True,'person_id':1}); assert r.status_code==200
    assert user.get('/api/v1/licenses').status_code==401
    user_me=login(user,'user'); assert user_me['stage']=='enroll'
    r=admin.put(f'/api/v1/accounts/{user_id}',headers=h,json={'role':'manager','active':False,'person_id':1}); assert r.status_code==200
    assert user.get('/api/v1/me').status_code==401

def test_email_reset_is_single_use(db,monkeypatch):
    import app as module
    sent=[]
    class SMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def starttls(self,context): pass
        def send_message(self,message): sent.append(message)
    monkeypatch.setattr(module.smtplib,'SMTP',SMTP)
    monkeypatch.setenv('SMTP_HOST','smtp.example.invalid'); monkeypatch.setenv('SMTP_FROM','kluis@example.invalid')
    user=TestClient(app); me=login(user,'user')
    assert db.post('/api/v1/password/reset-request',json={'username':'user'}).status_code==200
    assert len(sent)==1
    code=sent[0].get_content().split('\n\n')[1].strip()
    payload={'username':'user','code':code,'new_password':'heel-nieuw-wachtwoord'}
    assert db.post('/api/v1/password/reset',json=payload).status_code==200
    assert user.get('/api/v1/me').status_code==401
    assert db.post('/api/v1/password/reset',json=payload).status_code==400

def test_manager_cannot_manage_accounts(db):
    with Session(engine) as s:
        s.add(Account(username='manager',password_hash=ph.hash('een-lang-wachtwoord'),role='manager')); s.commit()
    pending=login(db,'manager'); h=headers(pending)
    setup=db.post('/api/v1/mfa/setup',headers=h,json={}).json()
    me=db.post('/api/v1/mfa/confirm',headers=h,json={'code':pyotp.TOTP(setup['secret']).now()}).json()
    assert db.get('/api/v1/persons').status_code==200
    assert db.get('/api/v1/accounts').status_code==403
    assert db.post('/api/v1/accounts',headers=headers(me),json={'username':'evil','password':'very-long-password','role':'admin'}).status_code==403

def test_auth_migration_keeps_accounts_and_revokes_sessions(tmp_path):
    import sqlite3, subprocess
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE accounts (id INTEGER PRIMARY KEY, username VARCHAR(120) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role VARCHAR(30) NOT NULL, active BOOLEAN NOT NULL, person_id INTEGER); CREATE TABLE sessions (token_hash VARCHAR(64) PRIMARY KEY, account_id INTEGER NOT NULL, csrf VARCHAR(100) NOT NULL, expires DATETIME NOT NULL); INSERT INTO accounts VALUES (1,'oud','hash','admin',1,NULL); INSERT INTO sessions VALUES ('old',1,'csrf','2030-01-01');")
    env=os.environ.copy(); env['DATABASE_URL']='sqlite:///'+str(path)
    cli=Path(__file__).resolve().parents[1]/'backend'/'cli.py'
    for _ in range(2): subprocess.run([sys.executable,str(cli),'migrate-auth'],env=env,check=True,capture_output=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT username FROM accounts').fetchone()[0]=='oud'
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert 'totp_secret' in {c[1] for c in conn.execute('PRAGMA table_info(accounts)')}
