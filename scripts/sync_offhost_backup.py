#!/usr/bin/env python3
"""Keep one verified, encrypted daily Lander recovery copy on this Mac."""
from datetime import datetime, timezone, timedelta
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess

from offhost_backup import download, verify

HOST = 'root@208.68.38.249'
REMOTE = '/opt/job-market-analytics/backups/airflow'
ARCHIVES = Path.home() / '.local/share/lander/recovery'
KEYS = Path.home() / '.config/lander/backup-keys'
PATTERN = r'job_analytics_(\d{4}-\d{2}-\d{2})\.dump'


def main():
    ARCHIVES.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (ARCHIVES / '.sync.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for partial in ARCHIVES.glob('job_analytics_*.dump.aes.partial'):
        stem = partial.name.removesuffix('.aes.partial')
        age = datetime.now(timezone.utc).timestamp() - partial.stat().st_mtime
        if re.fullmatch(PATTERN, stem) and age > 6*3600:
            partial.unlink()
            if not (ARCHIVES / (stem+'.aes')).exists():
                (KEYS / (stem.removesuffix('.dump')+'.key')).unlink(missing_ok=True)
    for old in ARCHIVES.glob('job_analytics_*.dump.aes'):
        match = re.fullmatch(PATTERN, old.name.removesuffix('.aes'))
        if match and datetime.now(timezone.utc)-datetime.strptime(match[1], '%Y-%m-%d').replace(tzinfo=timezone.utc) > timedelta(days=7):
            old.unlink()
            old.with_suffix('.json').unlink(missing_ok=True)
            (KEYS / (old.name.removesuffix('.dump.aes')+'.key')).unlink(missing_ok=True)
    listing = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
        HOST, f"find {REMOTE} -maxdepth 1 -type f -name 'job_analytics_*.dump' -printf '%f\\n'"],
        text=True, timeout=30)
    names = sorted(name for name in listing.splitlines() if re.fullmatch(PATTERN, name))
    if not names:
        raise RuntimeError('No daily recovery dump available')
    name = names[-1]
    day = datetime.strptime(re.fullmatch(PATTERN, name)[1], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc)-day > timedelta(days=2):
        raise RuntimeError('Daily server backup is over two days old')
    archive = ARCHIVES / (name+'.aes')
    key = KEYS / (name.removesuffix('.dump')+'.key')
    if archive.exists():
        result = verify(archive, key)
    else:
        if shutil.disk_usage(Path.home()).free < 2.5*1024**3:
            raise RuntimeError('Need at least 2.5 GiB free for a new backup; retaining the current copy')
        result = download(HOST, REMOTE+'/'+name, archive, key)
    # Remove only this tool's older named recovery copies after the new archive
    # has been authenticated and verified. Never touch unrelated backups.
    for old in ARCHIVES.glob('job_analytics_*.dump.aes'):
        stem = old.name.removesuffix('.aes')
        if old != archive and re.fullmatch(PATTERN, stem):
            old.unlink()
            old.with_suffix('.json').unlink(missing_ok=True)
            (KEYS / (stem.removesuffix('.dump')+'.key')).unlink(missing_ok=True)
    print(json.dumps({'archive': archive.name, **result}))


if __name__ == '__main__':
    main()
