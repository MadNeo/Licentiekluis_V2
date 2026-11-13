"""Voorlopige lokale kern van LicentieKluis; nog niet geschikt voor productie."""
import os, secrets, hashlib, datetime as dt
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, Boolean, ForeignKey, Text, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./licentiekluis.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
class Base(DeclarativeBase): pass
class Account(Base):
    __tablename__='accounts'
    id:Mapped[int]=mapped_column(primary_key=True)
    username:Mapped[str]=mapped_column(String(120), unique=True)
    password_hash:Mapped[str]=mapped_column(String(255))
    role:Mapped[str]=mapped_column(String(30))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    person_id:Mapped[int|None]=mapped_column(ForeignKey('persons.id'),nullable=True)
class Person(Base):
    __tablename__='persons'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(200))
    email:Mapped[str]=mapped_column(String(254),unique=True)
    department:Mapped[str]=mapped_column(String(120),default='')
    active:Mapped[bool]=mapped_column(Boolean,default=True)
class Product(Base):
    __tablename__='products'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(200))
    vendor:Mapped[str]=mapped_column(String(200))
class License(Base):
    __tablename__='licenses'
    id:Mapped[int]=mapped_column(primary_key=True)
    reference:Mapped[str]=mapped_column(String(100),unique=True)
    product_id:Mapped[int]=mapped_column(ForeignKey('products.id'))
    license_type:Mapped[str]=mapped_column(String(40))
    seats:Mapped[int]=mapped_column(Integer)
    end_date:Mapped[str|None]=mapped_column(String(10),nullable=True)
    encrypted_key:Mapped[str|None]=mapped_column(Text,nullable=True)
    archived:Mapped[bool]=mapped_column(Boolean,default=False)
class Assignment(Base):
    __tablename__='assignments'
    id:Mapped[int]=mapped_column(primary_key=True)
    license_id:Mapped[int]=mapped_column(ForeignKey('licenses.id'))
    person_id:Mapped[int]=mapped_column(ForeignKey('persons.id'))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
class LoginSession(Base):
    __tablename__='sessions'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    account_id:Mapped[int]=mapped_column(ForeignKey('accounts.id'))
    csrf:Mapped[str]=mapped_column(String(100))
    expires:Mapped[dt.datetime]=mapped_column()
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    account_id:Mapped[int]=mapped_column(Integer)
    action:Mapped[str]=mapped_column(String(100))
    created:Mapped[dt.datetime]=mapped_column(default=lambda:dt.datetime.now(dt.timezone.utc))

app=FastAPI(title='LicentieKluis API',version='0.1.0')
ph=PasswordHasher()
def db():
    with Session(engine) as s: yield s
def key():
    value=os.getenv('LICENTIEKLUIS_KEY')
    if not value: raise HTTPException(503,'Encryptiesleutel ontbreekt')
    try: return Fernet(value.encode())
    except Exception: raise HTTPException(503,'Ongeldige encryptiesleutel')
def digest(v): return hashlib.sha256(v.encode()).hexdigest()
def current(request:Request,s:Session=Depends(db)):
    token=request.cookies.get('lk_session','')
    sess=s.get(LoginSession,digest(token)) if token else None
    if not sess or sess.expires < dt.datetime.utcnow(): raise HTTPException(401,'Log eerst in')
    account=s.get(Account,sess.account_id)
    if not account or not account.active: raise HTTPException(401,'Account niet beschikbaar')
    if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),sess.csrf): raise HTTPException(403,'Ongeldig CSRF-token')
    return account
def manager(a:Account=Depends(current)):
    if a.role not in ('admin','manager'): raise HTTPException(403,'Onvoldoende rechten')
    return a
def audit(s,a,action): s.add(Audit(account_id=a.id,action=action))
class Login(BaseModel): username:str; password:str
class PersonIn(BaseModel): name:str=Field(min_length=1,max_length=200); email:str=Field(min_length=3,max_length=254); department:str=''
class LicenseIn(BaseModel):
    reference:str=Field(min_length=1,max_length=100); name:str=Field(min_length=1,max_length=200); vendor:str=Field(min_length=1,max_length=200); license_type:str='abonnement'; seats:int=Field(ge=1,le=1000000); end_date:dt.date|None=None; product_key:str|None=None
class AssignmentIn(BaseModel): license_id:int; person_id:int
@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request); response.headers['X-Content-Type-Options']='nosniff'; response.headers['X-Frame-Options']='DENY'; response.headers['Cache-Control']='no-store'; return response
@app.get('/api/v1/health/live')
def live(): return {'status':'ok'}
@app.get('/api/v1/health/ready')
def ready(s:Session=Depends(db)): s.execute(select(1)); return {'status':'ok'}
@app.post('/api/v1/login')
def login(data:Login,response:Response,s:Session=Depends(db)):
    a=s.scalar(select(Account).where(Account.username==data.username))
    try: valid=bool(a and a.active and ph.verify(a.password_hash,data.password))
    except Exception: valid=False
    if not valid: raise HTTPException(401,'Onjuiste gebruikersnaam of wachtwoord')
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    s.add(LoginSession(token_hash=digest(token),account_id=a.id,csrf=csrf,expires=dt.datetime.utcnow()+dt.timedelta(hours=8))); audit(s,a,'login'); s.commit()
    response.set_cookie('lk_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true')=='true',samesite='strict',max_age=28800,path='/')
    return {'username':a.username,'role':a.role,'csrf':csrf}
@app.get('/api/v1/me')
def me(request:Request,a:Account=Depends(current),s:Session=Depends(db)):
    session=s.get(LoginSession,digest(request.cookies['lk_session'])); return {'username':a.username,'role':a.role,'csrf':session.csrf}
@app.post('/api/v1/logout')
def logout(request:Request,response:Response,a:Account=Depends(current),s:Session=Depends(db)):
    sess=s.get(LoginSession,digest(request.cookies['lk_session'])); s.delete(sess); audit(s,a,'logout'); s.commit(); response.delete_cookie('lk_session',path='/'); return {'ok':True}
@app.get('/api/v1/persons')
def persons(a:Account=Depends(manager),s:Session=Depends(db)):
    return [{'id':p.id,'name':p.name,'email':p.email,'department':p.department,'active':p.active} for p in s.scalars(select(Person).order_by(Person.name))]
@app.post('/api/v1/persons')
def add_person(data:PersonIn,a:Account=Depends(manager),s:Session=Depends(db)):
    if s.scalar(select(Person).where(Person.email==data.email)): raise HTTPException(409,'E-mailadres bestaat al')
    p=Person(**data.model_dump()); s.add(p); audit(s,a,'person_created'); s.commit(); return {'id':p.id}
@app.get('/api/v1/licenses')
def licenses(a:Account=Depends(current),s:Session=Depends(db)):
    q=select(License,Product).join(Product)
    if a.role=='user': q=q.join(Assignment,Assignment.license_id==License.id).where(Assignment.person_id==a.person_id,Assignment.active==True)
    rows=s.execute(q.order_by(License.id.desc())).all(); out=[]
    for l,p in rows:
        used=s.scalar(select(func.count()).select_from(Assignment).where(Assignment.license_id==l.id,Assignment.active==True))
        out.append({'id':l.id,'reference':l.reference,'name':p.name,'vendor':p.vendor,'type':l.license_type,'seats':l.seats,'used':used,'end_date':l.end_date,'archived':l.archived,'has_key':bool(l.encrypted_key)})
    return out
@app.post('/api/v1/licenses')
def add_license(data:LicenseIn,a:Account=Depends(manager),s:Session=Depends(db)):
    if s.scalar(select(License).where(License.reference==data.reference)): raise HTTPException(409,'Referentie bestaat al')
    p=Product(name=data.name,vendor=data.vendor); s.add(p); s.flush(); l=License(reference=data.reference,product_id=p.id,license_type=data.license_type,seats=data.seats,end_date=data.end_date.isoformat() if data.end_date else None,encrypted_key=key().encrypt(data.product_key.encode()).decode() if data.product_key else None); s.add(l); audit(s,a,'license_created'); s.commit(); return {'id':l.id}
@app.post('/api/v1/assignments')
def assign(data:AssignmentIn,a:Account=Depends(manager),s:Session=Depends(db)):
    l=s.scalar(select(License).where(License.id==data.license_id).with_for_update()); p=s.get(Person,data.person_id)
    if not l or not p: raise HTTPException(404,'Licentie of persoon niet gevonden')
    if not p.active or l.archived or (l.end_date and l.end_date<dt.date.today().isoformat()): raise HTTPException(400,'Toewijzing niet toegestaan')
    if s.scalar(select(Assignment).where(Assignment.license_id==l.id,Assignment.person_id==p.id,Assignment.active==True)): raise HTTPException(409,'Al toegewezen')
    count=s.scalar(select(func.count()).select_from(Assignment).where(Assignment.license_id==l.id,Assignment.active==True))
    if count>=l.seats: raise HTTPException(409,'Geen seats beschikbaar')
    s.add(Assignment(license_id=l.id,person_id=p.id)); audit(s,a,'assignment_created'); s.commit(); return {'ok':True}
@app.post('/api/v1/licenses/{license_id}/key')
def reveal(license_id:int,a:Account=Depends(current),s:Session=Depends(db)):
    l=s.get(License,license_id)
    if not l: raise HTTPException(404,'Licentie niet gevonden')
    if a.role=='user' and not s.scalar(select(Assignment).where(Assignment.license_id==license_id,Assignment.person_id==a.person_id,Assignment.active==True)): raise HTTPException(403,'Onvoldoende rechten')
    if not l.encrypted_key: raise HTTPException(404,'Geen productsleutel aanwezig')
    value=key().decrypt(l.encrypted_key.encode()).decode(); audit(s,a,'key_revealed'); s.commit(); return {'key':value}
