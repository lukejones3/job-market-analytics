#!/usr/bin/env python3
"""Download an encrypted off-host recovery copy, or verify/decrypt that copy.

Uses SSH and AES-256-GCM. Store the key separately from the backup. Decryption
writes only to stdout so an operator can pipe directly into pg_restore.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER = b'LANDER-BACKUP-V1\n'
CHUNK = 1024 * 1024


def download(host, remote_path, archive, key_file):
    if not re.fullmatch(r'[\w.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    if not re.fullmatch(r'/opt/job-market-analytics/backups/airflow/job_analytics_\d{4}-\d{2}-\d{2}\.dump', remote_path):
        raise ValueError('Expected a daily Lander recovery dump')
    for path in [archive.parent, key_file.parent]:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = os.urandom(32)
    with key_file.open('xb') as target:
        os.chmod(key_file, 0o600)
        target.write(key)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    digest, size = hashlib.sha256(), 0
    temporary = archive.with_suffix(archive.suffix + '.partial')
    try:
        with temporary.open('xb') as target:
            os.chmod(temporary, 0o600)
            target.write(HEADER + nonce)
            process = subprocess.Popen(['ssh', host, 'cat', remote_path], stdout=subprocess.PIPE)
            with process.stdout as source:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    target.write(encryptor.update(chunk))
            if process.wait() != 0:
                raise RuntimeError('Backup download failed')
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
        temporary.rename(archive)
    except Exception:
        temporary.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
        raise
    metadata = {'source': remote_path, 'bytes': size, 'sha256': digest.hexdigest()}
    archive.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    verify(archive, key_file, expected=digest.hexdigest())
    return metadata


def verify(archive, key_file, expected=None, output=False):
    key = key_file.read_bytes()
    digest, size = hashlib.sha256(), 0
    with archive.open('rb') as source:
        if source.read(len(HEADER)) != HEADER:
            raise ValueError('Unknown archive format')
        nonce = source.read(12)
        source.seek(-16, 2)
        tag = source.read(16)
        source.seek(len(HEADER) + 12)
        remaining = archive.stat().st_size - len(HEADER) - 12 - 16
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        while remaining:
            chunk = source.read(min(CHUNK, remaining))
            if not chunk:
                raise ValueError('Truncated backup')
            remaining -= len(chunk)
            plain = decryptor.update(chunk)
            digest.update(plain)
            size += len(plain)
            if output:
                sys.stdout.buffer.write(plain)
        decryptor.finalize()
    if expected and digest.hexdigest() != expected:
        raise ValueError('Backup checksum mismatch')
    return {'verified': True, 'bytes': size, 'sha256': digest.hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['download', 'verify', 'decrypt'])
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--host', default='root@208.68.38.249')
    parser.add_argument('--remote-path')
    args = parser.parse_args()
    if args.action == 'download':
        print(json.dumps(download(args.host, args.remote_path or '', args.archive, args.key_file)))
    else:
        result = verify(args.archive, args.key_file, output=args.action == 'decrypt')
        if args.action == 'verify':
            print(json.dumps(result))
