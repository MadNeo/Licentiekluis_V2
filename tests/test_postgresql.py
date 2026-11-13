"""PostgreSQL-integratietests. Alleen met expliciet geconfigureerde testdatabase."""
import concurrent.futures
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException
from test_core import Base, Account, Person, ph
from app import Product, License, Assignment, AssignmentIn, assign, Audit, LoginSession, ready

ROOT=Path(__file__).resolve().parents[1]
TEST_URL=os.environ.get('LICENTIEKLUIS_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not TEST_URL,reason='LICENTIEKLUIS_TEST_DATABASE_URL ontbreekt')

@pytest.fixture
def pg(monkeypatch):
    admin=create_engine(TEST_URL)
    assert admin.url.database.endswith('_test'), 'Testdatabase moet eindigen op _test.'
    schema='lk_test_'+uuid.uuid4().hex
    with admin.begin() as c: c.execute(text(f'CREATE SCHEMA "{schema}"'))
    monkeypatch.setenv('PGOPTIONS',f'-csearch_path={schema}')
    monkeypatch.setenv('DATABASE_URL',TEST_URL)
    engine=None
    try:
        subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,check=True,capture_output=True)
        subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,check=True,capture_output=True)
        engine=create_engine(TEST_URL)
        yield engine
    finally:
        if engine is not None: engine.dispose()
        with admin.begin() as c: c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def setup_rows(engine):
    with Session(engine) as s:
        a=Account(username='beheerder',password_hash=ph.hash('een-lang-wachtwoord'),role='admin')
        p1=Person(name='A',email='a@example.invalid',department='IT')
        p2=Person(name='B',email='b@example.invalid',department='IT')
        product=Product(name='Office',vendor='Microsoft')
        s.add_all([a,p1,p2,product]);s.flush()
        license=License(reference='LK-1',product_id=product.id,license_type='abonnement',seats=1)
        s.add(license);s.commit()
        return a.id,p1.id,p2.id,license.id


def test_migrations_constraints_and_rollback(pg):
    check=subprocess.run([sys.executable,'-m','alembic','check'],cwd=ROOT,capture_output=True,text=True)
    assert check.returncode==0,(check.stdout,check.stderr)
    a,p1,p2,lid=setup_rows(pg)
    with pg.connect() as c:
        assert c.execute(text('select version_num from alembic_version')).scalar_one()=='27642dde2f04'
        assert not c.execute(text('select rolsuper from pg_roles where rolname=current_user')).scalar_one()
    with pytest.raises(IntegrityError), pg.begin() as c:
        c.execute(text("INSERT INTO assignments (license_id,person_id,active) VALUES (:l,999999,true)"),{'l':lid})
    with pytest.raises(IntegrityError), pg.begin() as c:
        c.execute(text('UPDATE licenses SET seats=0 WHERE id=:id'),{'id':lid})
    with pg.begin() as c:
        c.execute(text('INSERT INTO assignments (license_id,person_id,active) VALUES (:l,:p,true)'),{'l':lid,'p':p1})
    with pytest.raises(IntegrityError), pg.begin() as c:
        c.execute(text('INSERT INTO assignments (license_id,person_id,active) VALUES (:l,:p,true)'),{'l':lid,'p':p1})
    with Session(pg) as s:
        s.add(Person(name='Teruggedraaid',email='rollback@example.invalid',department='IT'));s.flush();s.rollback()
        assert not s.scalar(select(Person).where(Person.email=='rollback@example.invalid'))


def test_concurrent_last_seat(pg):
    aid,p1,p2,lid=setup_rows(pg)
    barrier=threading.Barrier(2)
    def attempt(pid):
        with Session(pg) as s:
            user=s.get(Account,aid)
            barrier.wait(timeout=10)
            try:
                assign(AssignmentIn(license_id=lid,person_id=pid),a=user,s=s)
                return 200
            except HTTPException as exc:
                s.rollback(); return exc.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,[p1,p2]))
    assert sorted(results)==[200,409]
    with Session(pg) as s:
        assert len(s.scalars(select(Assignment).where(Assignment.active==True)).all())==1
        assert len(s.scalars(select(Audit).where(Audit.action=='assignment_created')).all())==1



def test_concurrent_duplicate_assignment(pg):
    aid,p1,_,lid=setup_rows(pg)
    with pg.begin() as c: c.execute(text('UPDATE licenses SET seats=2 WHERE id=:id'),{'id':lid})
    barrier=threading.Barrier(2)
    def attempt(_):
        with Session(pg) as s:
            user=s.get(Account,aid); barrier.wait(timeout=10)
            try:
                assign(AssignmentIn(license_id=lid,person_id=p1),a=user,s=s); return 200
            except HTTPException as exc:
                s.rollback(); return exc.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==[200,409]
    with Session(pg) as s: assert len(s.scalars(select(Assignment)).all())==1

def test_sqlite_import_preserves_data_and_rejects_nonempty(pg,tmp_path,monkeypatch):
    source=tmp_path/'source.db'; src=create_engine('sqlite:///'+str(source)); Base.metadata.create_all(src)
    secret=Fernet.generate_key().decode(); monkeypatch.setenv('LICENTIEKLUIS_KEY',secret)
    with Session(src) as s:
        p=Person(name='Test',email='user@example.invalid',department='IT');product=Product(name='Office',vendor='Microsoft');s.add_all([p,product]);s.flush()
        a=Account(username='admin',password_hash=ph.hash('een-lang-wachtwoord'),role='admin');s.add(a);s.flush()
        l=License(reference='X',product_id=product.id,license_type='perpetual',seats=1,encrypted_key=Fernet(secret.encode()).encrypt(b'fictieve sleutel').decode());s.add(l);s.flush()
        s.add(Assignment(license_id=l.id,person_id=p.id));s.add(LoginSession(token_hash='a'*64,account_id=a.id,csrf='x',expires=__import__('datetime').datetime(2030,1,1),stage='full'));s.commit()
    src.dispose()
    cmd=[sys.executable,'scripts/import_sqlite.py','--source',str(source),'--confirm']
    result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode==0,(result.stdout,result.stderr)
    with Session(pg) as s:
        assert s.scalar(select(License).where(License.reference=='X')) is not None
        assert s.scalars(select(Assignment)).all()
        assert not s.scalars(select(LoginSession)).all()
        new=Person(name='Na import',email='next@example.invalid',department='IT');s.add(new);s.commit();assert new.id>1
    result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode!=0 and 'bevat al gegevens' in result.stderr


def test_import_with_wrong_key_rolls_back(pg,tmp_path,monkeypatch):
    source=tmp_path/'invalid.db'; src=create_engine('sqlite:///'+str(source)); Base.metadata.create_all(src)
    with Session(src) as s:
        product=Product(name='App',vendor='Test');s.add(product);s.flush()
        s.add(License(reference='secret',product_id=product.id,license_type='retail',seats=1,encrypted_key=Fernet(Fernet.generate_key()).encrypt(b'fake').decode()));s.commit()
    src.dispose(); monkeypatch.setenv('LICENTIEKLUIS_KEY',Fernet.generate_key().decode())
    result=subprocess.run([sys.executable,'scripts/import_sqlite.py','--source',str(source),'--confirm'],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True)
    assert result.returncode!=0 and 'sleutel past niet' in result.stderr
    with Session(pg) as s:
        assert s.scalars(select(Product)).all()==[]
        assert s.scalars(select(License)).all()==[]


def test_sqlite_import_rejects_oversubscribed_license(pg,tmp_path,monkeypatch):
    source=tmp_path/'invalid.db'; src=create_engine('sqlite:///'+str(source)); Base.metadata.create_all(src)
    with Session(src) as s:
        p1=Person(name='A',email='a@test.invalid',department='');p2=Person(name='B',email='b@test.invalid',department='');product=Product(name='App',vendor='Test');s.add_all([p1,p2,product]);s.flush()
        l=License(reference='oversubscribed',product_id=product.id,license_type='retail',seats=1);s.add(l);s.flush()
        s.add_all([Assignment(license_id=l.id,person_id=p1.id),Assignment(license_id=l.id,person_id=p2.id)]);s.commit()
    src.dispose()
    result=subprocess.run([sys.executable,'scripts/import_sqlite.py','--source',str(source),'--confirm'],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True)
    assert result.returncode!=0 and 'meer actieve toewijzingen' in result.stderr
    with Session(pg) as s: assert s.scalars(select(License)).all()==[]


def test_readiness_requires_current_migration(pg):
    with Session(pg) as s: assert ready(s)=={'status':'ok'}
    with pg.begin() as c: c.execute(text("UPDATE alembic_version SET version_num='old'"))
    with Session(pg) as s:
        with pytest.raises(HTTPException) as exc: ready(s)
        assert exc.value.status_code==503


def test_concurrent_requests_through_api(pg):
    import datetime
    from fastapi.testclient import TestClient
    import app as module
    aid,p1,p2,lid=setup_rows(pg)
    with Session(pg) as s:
        account=s.get(Account,aid); account.totp_secret='not-used-in-this-test'
        s.add_all([LoginSession(token_hash=module.digest('test-session-'+str(i)),account_id=aid,csrf='test-csrf',stage='full',expires=datetime.datetime.utcnow()+datetime.timedelta(minutes=5)) for i in range(2)]); s.commit()
    def override_db():
        with Session(pg) as s: yield s
    module.app.dependency_overrides[module.db]=override_db
    barrier=threading.Barrier(2)
    def attempt(pair):
        i,pid=pair
        with TestClient(module.app) as client:
            client.cookies.set('lk_session','test-session-'+str(i))
            barrier.wait(timeout=10)
            response=client.post('/api/v1/assignments',headers={'X-CSRF-Token':'test-csrf'},json={'license_id':lid,'person_id':pid})
            return response.status_code
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(attempt,enumerate([p1,p2])))==[200,409]
    finally:
        module.app.dependency_overrides.pop(module.db,None)
    with Session(pg) as s: assert len(s.scalars(select(Assignment)).all())==1
