# Scale repair release

This release fixes the schema cursor mismatch that stopped direct crawling,
makes nightly/discovery failures visible at the DAG result, isolates Common
Crawl failures, and updates the previously undeployed October supply pipeline.
Stored job embeddings are reused; the deployment does not regenerate them.

Apply `sql/scale_readiness.sql`, `sql/billing_lifecycle.sql`, and
`sql/product_lifecycle.sql` before deploying the matching web release.
The nightly schema task also maintains these migrations.

Billing reconciles each event against the current Stripe subscription under a
customer lock, records processed event IDs transactionally, and updates the
stable account. Cancellation changes entitlement without deactivating the free
account. Database failures return a retriable webhook response. No real payment
or subscription mutation is part of the validation fixtures.

`python/enforce_data_retention.py` is dry-run by default; nightly `--apply`
removes resumes older than 30 days with their cascading derived records. The
existing backup rotation retains daily dumps for at most eight days. Resume
metadata with no content also follows the same deletion rule.

`python/operational_health.py` reports aggregate inventory, classification gaps,
direct-host failures, notification backlog, retention, database connections and
disk usage. A nonzero exit means an operational check needs attention.
`python/outcome_reminders.py` reports eligible accounts by default. Delivery
requires both `--apply` and `OUTCOME_EMAIL_DELIVERY_ENABLED=1`; historical
applications older than 21 days are excluded. No outbound messages were sent
during this implementation.

## Verification and recovery

The October 24 daily custom-format backup was restored into the isolated
`lander_restore_20261025` database, including vector indexes and constraints.
All five real database tests passed: content timestamps, billing identity and
event deduplication, retention dry-run, retention cascades, and web-role cleanup/milestone permissions. Local Python
tests also exercise the responsive resume endpoint and production DAG imports.

Run database integration checks only against a disposable restore database:

```sh
LANDER_TEST_DB=lander_restore_YYYYMMDD python -m pytest tests/test_scale_database.py -v
```

`scripts/offhost_backup.py` downloads a daily backup over SSH and encrypts it
using AES-256-GCM. The archive and 256-bit key must be kept separately. Verify
before restoring; restore only into a new isolated database, with `vector`
created by its database administrator first. Preserve the original live DB.

```sh
python scripts/offhost_backup.py verify --archive BACKUP.dump.aes --key-file BACKUP.key
python scripts/offhost_backup.py decrypt --archive BACKUP.dump.aes --key-file BACKUP.key |
  pg_restore --no-owner --exit-on-error -d DISPOSABLE_RESTORE_DATABASE
```

Production rollback starts at the `scale-before-20261025` server tag. Additive
migrations are compatible with the prior code; do not drop billing ledgers or
content dates during an application rollback. Retention deletions require a
recovery restore if reversal is needed.

## Continuing capacity work

The default Google quota is 200 notification publishes per Pacific day. Fair
scheduling cannot deliver a larger daily lifecycle volume than the approved
quota. Monitor sends by class and queue age; obtain a quota increase when
needed. Successful notifications do not establish Google indexing.

The serverless connection pool still relies on direct PostgreSQL connections.
Connection pooling across instances and representative sustained load testing
remain capacity work. Source yield, classification coverage, employer identity
review and contact coverage require ongoing measured improvement rather than
removing publication gates. A clean release is not proof of growth readiness.

The repaired Python runtime and CI lockfiles pass vulnerability scanning. Clean
Linux runtime checks include all advertised PDF, DOCX and TXT resume formats;
`pdfplumber` is now an explicit dependency rather than an undeclared server
installation. The CPU-only PyTorch wheel uses the same model weights. Three
synthetic baseline vectors agreed above 0.99999 cosine similarity; stored job
embeddings were not regenerated.

An account-free synthetic resume check against the isolated API delivered the
correct Free/Pro limits while a concurrent health request completed in 31 ms.
This is an event-loop regression check, not a sustained capacity benchmark.

On the Mac, `com.lander.offhost-backup` refreshes the encrypted recovery copy
once a day while logged in. The archive is under `~/.local/share/lander/recovery`
and its separate key is under `~/.config/lander/backup-keys`; both stay outside
Git. The daily task keeps the latest verified copy and clears expired copies.
A successful decryption/checksum check matched the server's SHA-256. This is a
database recovery drill; complete host/credential disaster recovery still needs
its own rehearsal.
