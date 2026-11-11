# ADR-0013: Backups, restore drills and data at shutdown

## Status

Accepted

## Date

2026-11-11

## Sign-off

Owner: JaimeSolisS. Direction given, and the M9 plan approved, on 2026-11-11 ("nightly pg_dump plus EBS snapshots, not pgBackRest/PITR"; decision 8, a final PROD dump to an archive that survives destroy; decision 14). Escalation E4 of the plan (archive expiry): the owner chose option (a), 365 days with Glacier after 30. Recorded by M9-01 (#609).

## Context

Resolves architecture §10 **ISSUE-I7** in [`../architecture.md`](../architecture.md) §10.10, and changes spec [`../spec.md`](../spec.md) §10 "Backups" and W8.5.

- The spec planned pgBackRest to S3 with WAL archiving and PITR, plus restore drills before production reliance.
- PITR needs a custom PostgreSQL image, continuous WAL shipping and a repository bucket. For one small center with environments that live about 20 days, the owner chose a daily logical dump plus volume snapshots.
- PostgreSQL 18 runs in the `db` container on the encrypted data volume (ADR-0010). DEV holds synthetic data only. PROD would hold personal data of families, including minors.

## Decision

- **Nightly logical backup:**
  - A systemd timer on each app host runs `/opt/classora/bin/backup.sh` at 08:30 UTC.
  - The script runs `pg_dump --format=custom` **inside the `db` container**, so client and server versions always match. It streams the dump to `s3://<env ops bucket>/backups/<UTC date>/classora.dump` with SSE-S3, and writes one JSON result line (`event=backup`, `status`, `bytes`, `duration_s`) to the host log the collector ships (ADR-0014).
  - A failure exits non-zero, and the timer's result is logged too.
  - The bucket keeps backups 14 days (lifecycle rule), blocks public access, is encrypted and allows the instance role only `PutObject`/`GetObject`/`ListBucket` on its own prefix.
- **EBS snapshots:** an Amazon Data Lifecycle Manager policy snapshots each app host's data volume daily and keeps 7. Snapshots are crash-consistent (PostgreSQL recovers like after a power loss). They exist to restore a whole volume quickly. The dump is the primary recovery path.
- **Recovery objectives:** RPO up to 24 hours. RTO about 30 minutes for a dump restore or a snapshot volume swap on a small database. M10-04 (#152) records final values.
- **Restore drill:** `restore.sh <dump>` restores into a scratch PostgreSQL 18 container on the same host, on its own network and port, never the live database. It compares per-table row counts with the source (taken at dump time and stored next to the dump), then removes the scratch container. M9-08 (#616) drills it on DEV, and M10-05 (#153) on PROD. A snapshot drill attaches a volume created from a snapshot to a scratch container.
- **Data at shutdown (decision 8):**
  - Before PROD is destroyed, `final-dump.sh` writes a dump plus its row counts to the long-lived **archive bucket** (bootstrap root, `prevent_destroy`), which is separate from the per-environment backup buckets: versioned, encrypted, public access blocked, Glacier Flexible Retrieval after 30 days, **expired after 365 days** (plan E4 (a), decision 14).
  - `make tf-destroy ENV=prod` refuses unless that dump exists and a scratch restore of it matched the row counts.
  - DEV and monitoring are destroyed without a final dump.
- **No copy of PROD data reaches DEV, local machines or CI.** Restores happen on the environment's own host.

### Delivery

M9-05 (#613) creates the archive bucket; M9-08 (#616) the backup, snapshot and restore scripts and the DEV drill; M9-10 (#618) the PROD teardown guard.

## Consequences

- No PITR: up to a day of data can be lost. This is accepted for a single center and recorded as a spec change (W8.5).
- No custom PostgreSQL image. `postgres:18` stays as pinned by ADR-0001.
- `pg_dump` runs against the live database once a night, which is cheap at this size.
- About $0.20/month per environment (plan §4).
- The archive holds minors' personal data for up to a year after shutdown. M10-07 (#155) reviews this period.
- Spec §10 "Backups", W8.5 and architecture §9.4 were rewritten in M9-01 (#609). #153 (M10-05) is rescoped from a PITR drill to a dump-and-snapshot restore drill.

## Alternatives considered

- **pgBackRest with WAL archiving and PITR** (spec as written). Rejected by the owner: a custom image, WAL bucket and retention tuning, more than a 20-day environment needs.
- **Snapshots only.** Crash-consistent only, and they disappear with a destroyed environment unless copied out. They aren't portable to another PostgreSQL either.
- **Dump only.** Fine for recovery, but restoring a whole host is slower. Snapshots are nearly free.
- **AWS Backup.** Same snapshots with more configuration; no gain at this size.
- **An archive that never expires** (plan E4 (b)), or one kept **90 days** (plan E4 (c), Glacier's minimum storage charge). The owner chose a year, to be reviewed by M10-07 (#155).
