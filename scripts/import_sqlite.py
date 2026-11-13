#!/usr/bin/env python3
"""Eenmalige, transactionele import van een bestaande SQLite-ontwikkeldatabase."""
import argparse, datetime, os, sqlite3, sys
from pathlib import Path
from urllib.parse import quote
from sqlalchemy import create_engine, inspect, select, text, func
from sqlalchemy.exc import SQLAlchemyError
from cryptography.fernet import Fernet, InvalidToken
from alembic.migration import MigrationContext

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import Base, License, Assignment

TABLES=('persons','products','accounts','licenses','assignments','audit','recovery_codes')
CLEARED=('sessions','reset_codes','login_attempts')
OPTIONAL={'accounts':{'totp_secret','totp_pending','totp_last_counter'}}
EXPECTED_REVISION='27642dde2f04'

def import_data(source, target_url, encryption_key):
    source=Path(source).resolve(strict=True)
    if not source.is_file(): raise ValueError('Het bronbestand is geen regulier bestand.')
    engine=create_engine(target_url)
    if engine.dialect.name!='postgresql': raise ValueError('De doeldatabase moet PostgreSQL zijn.')
    uri='file:'+quote(str(source),safe='/')+'?mode=ro'
    try:
        with sqlite3.connect(uri,uri=True) as src,engine.begin() as dst:
            src.row_factory=sqlite3.Row
            src.execute('BEGIN')
            if MigrationContext.configure(dst).get_current_revision()!=EXPECTED_REVISION: raise ValueError('De doeldatabase heeft niet de verwachte Alembic-versie; voer eerst de migraties uit.')
            tables=inspect(dst).get_table_names()
            for name in (*TABLES,*CLEARED):
                if name not in tables: raise ValueError('Het doelschema is onvolledig.')
                dst.execute(text(f'LOCK TABLE "{name}" IN SHARE ROW EXCLUSIVE MODE'))
                if dst.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{name}")')).scalar_one(): raise ValueError('De doeldatabase bevat al gegevens; import is geweigerd.')
            source_tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required=set(TABLES)-{'recovery_codes'}
            if not required.issubset(source_tables): raise ValueError('De SQLite-bron mist vereiste tabellen.')
            cipher=None
            if encryption_key:
                try: cipher=Fernet(encryption_key.encode())
                except (ValueError,TypeError): raise ValueError('De encryptiesleutel is ongeldig.') from None
            counts={}
            for name in TABLES:
                if name not in source_tables: counts[name]=0; continue
                source_columns={r[1] for r in src.execute(f'PRAGMA table_info("{name}")')}
                table=Base.metadata.tables[name]
                required_columns={c.name for c in table.columns}-OPTIONAL.get(name,set())
                if not required_columns.issubset(source_columns): raise ValueError(f'De brontabel {name} mist vereiste kolommen.')
                count=0
                for row in src.execute(f'SELECT * FROM "{name}"'):
                    data={}
                    for col in table.columns:
                        if col.name not in source_columns: value=None
                        else: value=row[col.name]
                        if col.name=='totp_pending': value=None
                        if value is not None and col.type.python_type is bool:
                            if value not in (0,1): raise ValueError(f'Ongeldige booleaanse waarde in {name}.')
                            value=bool(value)
                        if value is not None and col.type.python_type is datetime.datetime: value=datetime.datetime.fromisoformat(value)
                        if value and (name,col.name) in (('accounts','totp_secret'),('licenses','encrypted_key')):
                            if cipher is None: raise ValueError('De oorspronkelijke LICENTIEKLUIS_KEY is vereist om versleutelde gegevens te controleren.')
                            try: cipher.decrypt(value.encode())
                            except (InvalidToken,ValueError): raise ValueError('De encryptiesleutel past niet bij de brongegevens.') from None
                        data[col.name]=value
                    dst.execute(table.insert().values(**data)); count+=1
                counts[name]=count
            used=(select(Assignment.license_id, func.count().label('used')).where(Assignment.active.is_(True)).group_by(Assignment.license_id).subquery())
            if dst.execute(select(License.id).join(used,used.c.license_id==License.id).where(used.c.used>License.seats).limit(1)).first():
                raise ValueError('De bron bevat licenties met meer actieve toewijzingen dan seats.')
            for name in ('persons','products','accounts','licenses','assignments','audit'):
                dst.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE((SELECT MAX(id) FROM {name}), 1), EXISTS(SELECT 1 FROM {name}))"))
            return counts
    finally: engine.dispose()

def main():
    parser=argparse.ArgumentParser(description='Kopieer een lokale SQLite-database naar een lege PostgreSQL-database.')
    parser.add_argument('--source',required=True,help='Pad naar het bestaande SQLite-bestand (wordt alleen gelezen).')
    parser.add_argument('--confirm',action='store_true',help='Bevestig de eenmalige import naar de geconfigureerde lege database.')
    args=parser.parse_args()
    if not args.confirm: parser.error('Gebruik --confirm na controle van bron en doel.')
    url=os.getenv('DATABASE_URL')
    if not url: parser.error('Stel DATABASE_URL voor de PostgreSQL-doeldatabase in.')
    try: counts=import_data(args.source,url,os.getenv('LICENTIEKLUIS_KEY'))
    except (ValueError,FileNotFoundError) as exc: print(f'Import niet uitgevoerd: {exc}',file=sys.stderr); return 1
    except (SQLAlchemyError,sqlite3.Error): print('Import mislukt door een database- of integriteitsfout; er zijn geen gegevens geïmporteerd.',file=sys.stderr); return 1
    print('Import voltooid. Aantallen: '+', '.join(f'{k}={v}' for k,v in counts.items()))
    print('Sessies, resetcodes en lopende TOTP-instellingen zijn niet overgenomen.')
    return 0
if __name__=='__main__': sys.exit(main())
