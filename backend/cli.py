import getpass, argparse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import Base,engine,Account,Person,ph
p=argparse.ArgumentParser(); p.add_argument('command',choices=['init','account']); p.add_argument('--username'); p.add_argument('--role',choices=['admin','manager','user'],default='admin'); p.add_argument('--person-id',type=int); args=p.parse_args()
if args.command=='init': Base.metadata.create_all(engine); print('Database aangemaakt.')
else:
    if not args.username: p.error('--username is vereist')
    password=getpass.getpass('Nieuw wachtwoord (minimaal 12 tekens): ')
    if len(password)<12 or password!=getpass.getpass('Herhaal wachtwoord: '): raise SystemExit('Wachtwoorden ongeldig of verschillend.')
    with Session(engine) as s:
        if args.role=='user' and (not args.person_id or not s.get(Person,args.person_id)): raise SystemExit('Een bestaande --person-id is vereist voor een gebruiker.')
        if s.scalar(select(Account).where(Account.username==args.username)): raise SystemExit('Account bestaat al.')
        s.add(Account(username=args.username,password_hash=ph.hash(password),role=args.role,person_id=args.person_id)); s.commit(); print('Account aangemaakt.')
