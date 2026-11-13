import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from app import Base

config=context.config
target_metadata=Base.metadata

def run_migrations_offline():
    context.configure(url=os.getenv('DATABASE_URL', config.get_main_option('sqlalchemy.url')),target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    options=config.get_section(config.config_ini_section) or {}
    options['sqlalchemy.url']=os.getenv('DATABASE_URL',options['sqlalchemy.url'])
    connectable=engine_from_config(options,prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
