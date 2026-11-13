"""Lokale beheercommando's; alleen toegankelijk voor de systeembeheerder."""
import getpass, argparse
from sqlalchemy import select, delete, inspect, text
from sqlalchemy.orm import Session
from app import Base,engine,Account,Person,LoginSession,RecoveryCode,Audit,ph

p=argparse.ArgumentParser(); p.add_argument('command',choices=['init','migrate-auth','account','reset-password','reset-mfa']); p.add_argument('--username'); p.add_argument('--role',choices=['admin','manager','user'],default='admin'); p.add_argument('--person-id',type=int); args=p.parse_args()
if args.command=='init':
    Base.metadata.create_all(engine); print('Database aangemaakt. Voor bestaande databases: voer migrate-auth uit.')
elif args.command=='migrate-auth':
    # Only the three nullable columns added to the initial development schema are changed.
    with engine.begin() as connection:
        schema=inspect(connection)
        if 'accounts' not in schema.get_table_names(): raise SystemExit('Initialiseer eerst de database.')
        columns={c['name'] for c in schema.get_columns('accounts')}
        for column,kind in [('totp_secret','TEXT'),('totp_pending','TEXT'),('totp_last_counter','INTEGER')]:
            if column not in columns: connection.execute(text(f'ALTER TABLE accounts ADD COLUMN {column} {kind}'))
        session_columns={c['name'] for c in inspect(connection).get_columns('sessions')}
        if 'stage' not in session_columns: connection.execute(text("ALTER TABLE sessions ADD COLUMN stage VARCHAR(20) NOT NULL DEFAULT 'full'"))
        # Old sessions predate mandatory MFA; invalidate all of them.
        connection.execute(text('DELETE FROM sessions'))
    Base.metadata.create_all(engine); print('Beveiligingsschema bijgewerkt en bestaande sessies ingetrokken.')
else:
    if not args.username: p.error('--username is vereist')
    with Session(engine) as s:
        a=s.scalar(select(Account).where(Account.username==args.username))
        if args.command=='account':
            if a: raise SystemExit('Account bestaat al.')
            if args.role=='user' and (not args.person_id or not s.get(Person,args.person_id)): raise SystemExit('Een bestaande --person-id is vereist voor een gebruiker.')
            password=getpass.getpass('Nieuw wachtwoord (minimaal 12 tekens): ')
            if len(password)<12 or password!=getpass.getpass('Herhaal wachtwoord: '): raise SystemExit('Wachtwoorden ongeldig of verschillend.')
            a=Account(username=args.username,password_hash=ph.hash(password),role=args.role,person_id=args.person_id); s.add(a); s.flush(); s.add(Audit(account_id=a.id,action='local_account_created'))
        else:
            if not a: raise SystemExit('Account niet gevonden.')
            if args.command=='reset-password':
                password=getpass.getpass('Nieuw wachtwoord (minimaal 12 tekens): ')
                if len(password)<12 or password!=getpass.getpass('Herhaal wachtwoord: '): raise SystemExit('Wachtwoorden ongeldig of verschillend.')
                a.password_hash=ph.hash(password)
            else:
                a.totp_secret=None; a.totp_pending=None; a.totp_last_counter=None; s.execute(delete(RecoveryCode).where(RecoveryCode.account_id==a.id))
            s.execute(delete(LoginSession).where(LoginSession.account_id==a.id)); s.add(Audit(account_id=a.id,action='local_'+args.command.replace('-','_')))
        s.commit(); print('Actie uitgevoerd.')
