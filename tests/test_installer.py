import os
import pty
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select as sql_select
from sqlalchemy.orm import Session
from test_core import Account, ph


@pytest.fixture
def installation(tmp_path):
    project=Path(__file__).resolve().parents[1]
    shutil.copytree(project/'backend',tmp_path/'backend',ignore=shutil.ignore_patterns('__pycache__','*.db'))
    shutil.copy(project/'install.sh',tmp_path/'install.sh')
    (tmp_path/'.venv').symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
    database=tmp_path/'installer.db'
    env=os.environ.copy(); env['DATABASE_URL']='sqlite:///'+str(database)
    return tmp_path,database,env


def run_interactive(path,env):
    master,slave=pty.openpty()
    process=subprocess.Popen([str(path/'install.sh'),'--zonder-afhankelijkheden'],cwd=path,env=env,stdin=slave,stdout=slave,stderr=slave,close_fds=True)
    os.close(slave)
    transcript=b''; remaining=[(b'Gebruikersnaam voor de eerste beheerder:',b'eerste-admin\n'),(b'Nieuw wachtwoord',b'geheim-test-wachtwoord\n'),(b'Herhaal wachtwoord:',b'geheim-test-wachtwoord\n')]
    deadline=time.monotonic()+30
    try:
        while time.monotonic()<deadline:
            ready,_,_=select.select([master],[],[],0.2)
            if ready:
                try: chunk=os.read(master,4096)
                except OSError: break
                transcript+=chunk
                if remaining and remaining[0][0] in transcript:
                    _,reply=remaining.pop(0); os.write(master,reply)
            if process.poll() is not None and not ready: break
        process.wait(timeout=5)
        assert not remaining,transcript.decode(errors='replace')
        return process.returncode,transcript
    finally:
        if process.poll() is None: process.kill(); process.wait()
        os.close(master)


def test_first_install_and_repeated_install(installation):
    path,database,env=installation
    result,output=run_interactive(path,env)
    assert result==0
    assert b'geheim-test-wachtwoord' not in output
    engine=create_engine('sqlite:///'+str(database))
    with Session(engine) as session:
        account=session.scalar(sql_select(Account).where(Account.username=='eerste-admin'))
        assert account.role=='admin' and ph.verify(account.password_hash,'geheim-test-wachtwoord')
        original_hash=account.password_hash
    again=subprocess.run([str(path/'install.sh'),'--zonder-afhankelijkheden'],cwd=path,env=env,capture_output=True,text=True,timeout=30)
    assert again.returncode==0
    assert 'bestaat al een beheerder' in again.stdout
    with Session(engine) as session:
        accounts=session.scalars(sql_select(Account)).all()
        assert len(accounts)==1 and accounts[0].password_hash==original_hash
    engine.dispose()


def test_no_terminal_cannot_bootstrap_admin(installation):
    path,database,env=installation
    result=subprocess.run([str(path/'install.sh'),'--zonder-afhankelijkheden'],cwd=path,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0
    assert 'interactieve terminal' in result.stderr
    engine=create_engine('sqlite:///'+str(database))
    with Session(engine) as session:
        assert session.scalars(sql_select(Account)).all()==[]
    engine.dispose()
