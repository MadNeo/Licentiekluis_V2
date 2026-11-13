"""Voorlopige lokale kern van LicentieKluis; nog niet geschikt voor productie."""
import os, secrets, hashlib, datetime as dt, hmac, smtplib, ssl
from email.message import EmailMessage
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, Boolean, ForeignKey, Text, CheckConstraint, Index, event, select, func, delete, update, or_, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from argon2 import PasswordHasher
from cryptography.fernet import Fernet
import pyotp

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./licentiekluis.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
class Base(DeclarativeBase): pass
class Account(Base):
    __tablename__='accounts'
    __table_args__=(CheckConstraint("role IN ('admin', 'manager', 'user')",name='ck_accounts_role'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    username:Mapped[str]=mapped_column(String(120), unique=True)
    password_hash:Mapped[str]=mapped_column(String(255))
    role:Mapped[str]=mapped_column(String(30))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    person_id:Mapped[int|None]=mapped_column(ForeignKey('persons.id'),nullable=True)
    totp_secret:Mapped[str|None]=mapped_column(Text,nullable=True)
    totp_pending:Mapped[str|None]=mapped_column(Text,nullable=True)
    totp_last_counter:Mapped[int|None]=mapped_column(Integer,nullable=True)
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
    __table_args__=(CheckConstraint('seats > 0',name='ck_licenses_seats_positive'),)
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
    __table_args__=(Index('uq_assignments_active_license_person','license_id','person_id',unique=True,postgresql_where=text('active = true'),sqlite_where=text('active = 1')),Index('ix_assignments_license_active','license_id','active'))
    id:Mapped[int]=mapped_column(primary_key=True)
    license_id:Mapped[int]=mapped_column(ForeignKey('licenses.id'))
    person_id:Mapped[int]=mapped_column(ForeignKey('persons.id'))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
class LoginSession(Base):
    __tablename__='sessions'
    __table_args__=(CheckConstraint("stage IN ('full', 'enroll', 'verify')",name='ck_sessions_stage'),Index('ix_sessions_account_id','account_id'))
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    account_id:Mapped[int]=mapped_column(ForeignKey('accounts.id'))
    csrf:Mapped[str]=mapped_column(String(100))
    expires:Mapped[dt.datetime]=mapped_column()
    stage:Mapped[str]=mapped_column(String(20),default='full')
class RecoveryCode(Base):
    __tablename__='recovery_codes'
    code_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    account_id:Mapped[int]=mapped_column(ForeignKey('accounts.id',ondelete='CASCADE'),index=True)
class LoginAttempt(Base):
    __tablename__='login_attempts'
    id:Mapped[int]=mapped_column(primary_key=True)
    username_hash:Mapped[str]=mapped_column(String(64),index=True)
    source_hash:Mapped[str]=mapped_column(String(64),index=True)
    created:Mapped[dt.datetime]=mapped_column(index=True)
class ResetCode(Base):
    __tablename__='reset_codes'
    code_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    account_id:Mapped[int]=mapped_column(ForeignKey('accounts.id',ondelete='CASCADE'),index=True)
    expires:Mapped[dt.datetime]=mapped_column()
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    account_id:Mapped[int]=mapped_column(ForeignKey('accounts.id'))
    action:Mapped[str]=mapped_column(String(100))
    created:Mapped[dt.datetime]=mapped_column(default=lambda:dt.datetime.now(dt.timezone.utc))

app=FastAPI(title='LicentieKluis API',version='0.1.0')
ph=PasswordHasher()
dummy_password_hash=ph.hash(secrets.token_urlsafe(32))
def db():
    with Session(engine) as s: yield s
def key():
    value=os.getenv('LICENTIEKLUIS_KEY')
    if not value: raise HTTPException(503,'Encryptiesleutel ontbreekt')
    try: return Fernet(value.encode())
    except Exception: raise HTTPException(503,'Ongeldige encryptiesleutel')
def digest(v): return hashlib.sha256(v.encode()).hexdigest()
def session_account(request:Request,s:Session):
    token=request.cookies.get('lk_session','')
    sess=s.get(LoginSession,digest(token)) if token else None
    if not sess or sess.expires < dt.datetime.utcnow(): raise HTTPException(401,'Log eerst in')
    account=s.get(Account,sess.account_id)
    if not account or not account.active: raise HTTPException(401,'Account niet beschikbaar')
    if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),sess.csrf): raise HTTPException(403,'Ongeldig CSRF-token')
    return account,sess
def current(request:Request,s:Session=Depends(db)):
    account,sess=session_account(request,s)
    if sess.stage!='full': raise HTTPException(403,'Tweefactorauthenticatie vereist')
    if account.role in ('admin','manager') and not account.totp_secret: raise HTTPException(403,'Tweefactorauthenticatie instellen is vereist')
    return account
def admin(a:Account=Depends(current)):
    if a.role!='admin': raise HTTPException(403,'Onvoldoende rechten')
    return a
def manager(a:Account=Depends(current)):
    if a.role not in ('admin','manager'): raise HTTPException(403,'Onvoldoende rechten')
    return a
def audit(s,a,action): s.add(Audit(account_id=a.id,action=action))
class Login(BaseModel): username:str=Field(max_length=120); password:str=Field(max_length=1024)
class CodeIn(BaseModel): code:str=Field(min_length=6,max_length=40)
class PasswordChange(BaseModel): current_password:str; new_password:str=Field(min_length=12,max_length=1024)
class AccountIn(BaseModel): username:str=Field(min_length=1,max_length=120); password:str=Field(min_length=12,max_length=1024); role:str; person_id:int|None=None
class AccountUpdate(BaseModel): role:str; active:bool; person_id:int|None=None
class ResetRequest(BaseModel): username:str=Field(min_length=1,max_length=120)
class ResetPassword(BaseModel): username:str=Field(min_length=1,max_length=120); code:str=Field(min_length=16,max_length=128); new_password:str=Field(min_length=12,max_length=1024)
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
def ready(s:Session=Depends(db)):
    try:
        current_revision=MigrationContext.configure(s.connection()).get_current_revision()
        expected_revision=ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))).get_current_head()
        if not current_revision or current_revision!=expected_revision: raise HTTPException(503,'Database niet gereed')
    except SQLAlchemyError:
        raise HTTPException(503,'Database niet gereed') from None
    return {'status':'ok'}
def check_rate(s,username,request):
    now=dt.datetime.utcnow(); cutoff=now-dt.timedelta(minutes=15)
    source=request.client.host if request.client else 'unknown'
    uh=digest(username.casefold()); sh=digest(source)
    # Count both account and network origin independently, including nonexistent accounts.
    if s.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.username_hash==uh,LoginAttempt.created>=cutoff))>=5 or s.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.source_hash==sh,LoginAttempt.created>=cutoff))>=30:
        raise HTTPException(429,'Te veel pogingen. Probeer het later opnieuw.')
    return uh,sh,now
def failed(s,values):
    uh,sh,now=values; s.add(LoginAttempt(username_hash=uh,source_hash=sh,created=now)); s.commit()
def verify_password(a,password):
    try: return bool(ph.verify(a.password_hash if a else dummy_password_hash,password) and a)
    except Exception: return False
def verify_factor(s,a,code):
    if a.totp_secret and code.isdigit() and len(code)==6:
        counter=int(dt.datetime.now(dt.timezone.utc).timestamp())//30
        totp=pyotp.TOTP(key().decrypt(a.totp_secret.encode()).decode())
        for c in (counter-1,counter,counter+1):
            if hmac.compare_digest(totp.at(c*30),code):
                result=s.execute(update(Account).where(Account.id==a.id,or_(Account.totp_last_counter.is_(None),Account.totp_last_counter<c)).values(totp_last_counter=c))
                if result.rowcount==1: return True
    hashed=digest(code.strip().upper())
    result=s.execute(delete(RecoveryCode).where(RecoveryCode.account_id==a.id,RecoveryCode.code_hash==hashed))
    return result.rowcount==1
@app.post('/api/v1/login')
def login(data:Login,response:Response,request:Request,s:Session=Depends(db)):
    rate=check_rate(s,data.username,request)
    a=s.scalar(select(Account).where(Account.username==data.username))
    valid=verify_password(a,data.password) and a.active
    if not valid:
        failed(s,rate); raise HTTPException(401,'Onjuiste gebruikersnaam of wachtwoord')
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    stage='verify' if a.totp_secret else ('enroll' if a.role in ('admin','manager') else 'full')
    s.add(LoginSession(token_hash=digest(token),account_id=a.id,csrf=csrf,stage=stage,expires=dt.datetime.utcnow()+dt.timedelta(minutes=10) if stage!='full' else dt.datetime.utcnow()+dt.timedelta(hours=8))); audit(s,a,'login_password_verified'); s.commit()
    response.set_cookie('lk_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true')=='true',samesite='strict',max_age=28800,path='/')
    return {'username':a.username,'role':a.role,'csrf':csrf,'stage':stage,'mfa':bool(a.totp_secret)}
@app.get('/api/v1/me')
def me(request:Request,s:Session=Depends(db)):
    a,session=session_account(request,s); return {'username':a.username,'role':a.role,'csrf':session.csrf,'stage':session.stage,'mfa':bool(a.totp_secret)}
@app.post('/api/v1/logout')
def logout(request:Request,response:Response,s:Session=Depends(db)):
    a,_=session_account(request,s)
    sess=s.get(LoginSession,digest(request.cookies['lk_session'])); s.delete(sess); audit(s,a,'logout'); s.commit(); response.delete_cookie('lk_session',path='/'); return {'ok':True}
def promote(response,s,sess,a):
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    s.delete(sess); s.add(LoginSession(token_hash=digest(token),account_id=a.id,csrf=csrf,stage='full',expires=dt.datetime.utcnow()+dt.timedelta(hours=8)))
    audit(s,a,'login_mfa_verified'); s.commit()
    response.set_cookie('lk_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true')=='true',samesite='strict',max_age=28800,path='/')
    return {'username':a.username,'role':a.role,'csrf':csrf,'stage':'full','mfa':bool(a.totp_secret)}
@app.post('/api/v1/mfa/setup')
def setup_mfa(request:Request,s:Session=Depends(db)):
    a,sess=session_account(request,s)
    if a.totp_secret: raise HTTPException(409,'Tweefactorauthenticatie is al actief')
    if sess.stage not in ('full','enroll'): raise HTTPException(403,'Niet toegestaan')
    secret=pyotp.random_base32(); a.totp_pending=key().encrypt(secret.encode()).decode(); s.commit()
    return {'secret':secret,'uri':pyotp.TOTP(secret).provisioning_uri(name=a.username,issuer_name='LicentieKluis')}
@app.post('/api/v1/mfa/confirm')
def confirm_mfa(data:CodeIn,request:Request,response:Response,s:Session=Depends(db)):
    a,sess=session_account(request,s); rate=check_rate(s,a.username,request)
    if not a.totp_pending or a.totp_secret: raise HTTPException(400,'Start eerst de instelling')
    secret=key().decrypt(a.totp_pending.encode()).decode()
    if not pyotp.TOTP(secret).verify(data.code,valid_window=1): failed(s,rate); raise HTTPException(401,'Ongeldige code')
    a.totp_secret=a.totp_pending; a.totp_pending=None; a.totp_last_counter=int(dt.datetime.now(dt.timezone.utc).timestamp())//30
    codes=[secrets.token_hex(8).upper() for _ in range(10)]
    s.execute(delete(RecoveryCode).where(RecoveryCode.account_id==a.id))
    s.add_all([RecoveryCode(account_id=a.id,code_hash=digest(code)) for code in codes]); audit(s,a,'mfa_enabled')
    result=promote(response,s,sess,a); result['recovery_codes']=codes; return result
@app.post('/api/v1/mfa/verify')
def verify_mfa(data:CodeIn,request:Request,response:Response,s:Session=Depends(db)):
    a,sess=session_account(request,s)
    if sess.stage!='verify': raise HTTPException(400,'Geen tweede factor vereist')
    rate=check_rate(s,a.username,request)
    if not verify_factor(s,a,data.code): failed(s,rate); raise HTTPException(401,'Ongeldige code')
    return promote(response,s,sess,a)
@app.post('/api/v1/password')
def change_password(data:PasswordChange,request:Request,a:Account=Depends(current),s:Session=Depends(db)):
    rate=check_rate(s,a.username,request)
    if not verify_password(a,data.current_password): failed(s,rate); raise HTTPException(401,'Onjuist wachtwoord')
    a.password_hash=ph.hash(data.new_password); s.execute(delete(LoginSession).where(LoginSession.account_id==a.id)); audit(s,a,'password_changed'); s.commit(); return {'ok':True}
@app.post('/api/v1/password/reset-request')
def request_reset(data:ResetRequest,request:Request,s:Session=Depends(db)):
    rate=check_rate(s,'reset:'+data.username,request); failed(s,rate)
    account=s.scalar(select(Account).where(Account.username==data.username,Account.active==True))
    person=s.get(Person,account.person_id) if account and account.person_id else None
    host=os.getenv('SMTP_HOST'); sender=os.getenv('SMTP_FROM')
    if host and sender and person and person.active:
        code=secrets.token_hex(24).upper(); message=EmailMessage()
        message['Subject']='LicentieKluis wachtwoordherstel'; message['From']=sender; message['To']=person.email
        message.set_content('Gebruik deze eenmalige herstelcode binnen 15 minuten in LicentieKluis:\n\n'+code+'\n\nNegeer dit bericht als u geen herstel heeft aangevraagd.')
        try:
            with smtplib.SMTP(host,int(os.getenv('SMTP_PORT','587')),timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if os.getenv('SMTP_USER'): smtp.login(os.environ['SMTP_USER'],os.environ.get('SMTP_PASSWORD',''))
                smtp.send_message(message)
            s.execute(delete(ResetCode).where(ResetCode.account_id==account.id))
            s.add(ResetCode(code_hash=digest(code),account_id=account.id,expires=dt.datetime.utcnow()+dt.timedelta(minutes=15))); audit(s,account,'password_reset_requested'); s.commit()
        except (OSError,smtplib.SMTPException,ValueError):
            s.rollback()
    return {'ok':True,'message':'Als herstel beschikbaar is, ontvangt u een code.'}
@app.post('/api/v1/password/reset')
def reset_password(data:ResetPassword,request:Request,s:Session=Depends(db)):
    rate=check_rate(s,'reset:'+data.username,request)
    account=s.scalar(select(Account).where(Account.username==data.username,Account.active==True))
    token=s.get(ResetCode,digest(data.code.strip().upper()))
    if not account or not token or token.account_id!=account.id or token.expires<dt.datetime.utcnow():
        failed(s,rate); raise HTTPException(400,'Ongeldige of verlopen herstelcode')
    result=s.execute(delete(ResetCode).where(ResetCode.code_hash==token.code_hash))
    if result.rowcount!=1: raise HTTPException(400,'Ongeldige of verlopen herstelcode')
    account.password_hash=ph.hash(data.new_password); s.execute(delete(ResetCode).where(ResetCode.account_id==account.id)); s.execute(delete(LoginSession).where(LoginSession.account_id==account.id)); audit(s,account,'password_reset'); s.commit(); return {'ok':True}
@app.get('/api/v1/accounts')
def list_accounts(a:Account=Depends(admin),s:Session=Depends(db)):
    return [{'id':x.id,'username':x.username,'role':x.role,'active':x.active,'person_id':x.person_id,'mfa':bool(x.totp_secret)} for x in s.scalars(select(Account).order_by(Account.username))]
@app.post('/api/v1/accounts')
def create_account(data:AccountIn,a:Account=Depends(admin),s:Session=Depends(db)):
    if data.role not in ('admin','manager','user'): raise HTTPException(422,'Ongeldige rol')
    if data.person_id and not s.get(Person,data.person_id): raise HTTPException(422,'Persoon niet gevonden')
    if data.role=='user' and not data.person_id: raise HTTPException(422,'Koppel een persoon aan de gebruiker')
    if s.scalar(select(Account).where(Account.username==data.username)): raise HTTPException(409,'Account bestaat al')
    item=Account(username=data.username,password_hash=ph.hash(data.password),role=data.role,person_id=data.person_id); s.add(item)
    try:
        s.flush(); audit(s,a,f'account_created:{item.id}'); s.commit()
    except IntegrityError:
        s.rollback(); raise HTTPException(409,'Account of koppeling bestaat niet of is al in gebruik') from None
    return {'id':item.id}
@app.put('/api/v1/accounts/{account_id}')
def update_account(account_id:int,data:AccountUpdate,a:Account=Depends(admin),s:Session=Depends(db)):
    item=s.get(Account,account_id)
    if not item: raise HTTPException(404,'Account niet gevonden')
    if data.role not in ('admin','manager','user'): raise HTTPException(422,'Ongeldige rol')
    if data.person_id and not s.get(Person,data.person_id): raise HTTPException(422,'Persoon niet gevonden')
    if data.role=='user' and not data.person_id: raise HTTPException(422,'Koppel een persoon aan de gebruiker')
    if item.id==a.id and (not data.active or data.role!='admin'): raise HTTPException(400,'Eigen beheerrechten kunnen niet worden verwijderd')
    if (item.role,item.active,item.person_id)!=(data.role,data.active,data.person_id):
        item.role=data.role; item.active=data.active; item.person_id=data.person_id
        s.execute(delete(LoginSession).where(LoginSession.account_id==item.id)); audit(s,a,f'account_updated:{item.id}'); s.commit()
    return {'ok':True}
@app.get('/api/v1/persons')
def persons(a:Account=Depends(manager),s:Session=Depends(db)):
    return [{'id':p.id,'name':p.name,'email':p.email,'department':p.department,'active':p.active} for p in s.scalars(select(Person).order_by(Person.name))]
@app.post('/api/v1/persons')
def add_person(data:PersonIn,a:Account=Depends(manager),s:Session=Depends(db)):
    if s.scalar(select(Person).where(Person.email==data.email)): raise HTTPException(409,'E-mailadres bestaat al')
    p=Person(**data.model_dump()); s.add(p); audit(s,a,'person_created')
    try: s.commit()
    except IntegrityError:
        s.rollback(); raise HTTPException(409,'E-mailadres bestaat al') from None
    return {'id':p.id}
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
    p=Product(name=data.name,vendor=data.vendor); s.add(p); s.flush(); l=License(reference=data.reference,product_id=p.id,license_type=data.license_type,seats=data.seats,end_date=data.end_date.isoformat() if data.end_date else None,encrypted_key=key().encrypt(data.product_key.encode()).decode() if data.product_key else None); s.add(l); audit(s,a,'license_created')
    try: s.commit()
    except IntegrityError:
        s.rollback(); raise HTTPException(409,'Referentie bestaat al of licentiegegevens zijn ongeldig') from None
    return {'id':l.id}
@app.post('/api/v1/assignments')
def assign(data:AssignmentIn,a:Account=Depends(manager),s:Session=Depends(db)):
    l=s.scalar(select(License).where(License.id==data.license_id).with_for_update()); p=s.scalar(select(Person).where(Person.id==data.person_id).with_for_update())
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
