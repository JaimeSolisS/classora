# Runbook: backups, restore drills and the final dump

How the app hosts back up their database, how to prove a backup restores, how to restore for real, and the final dump before PROD is destroyed. The decisions are in [ADR-0013](../adr/0013-backups-restore-drills-and-shutdown-data.md). Host access (Session Manager, `sudo -i`) is in [`host.md`](host.md).

Every command from your own machine uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`. Host commands run as root on the host.

## What runs

| What | When | Where | Kept |
|---|---|---|---|
| `classora-backup.timer` → `/opt/classora/bin/backup.sh` | 08:30 UTC (up to 5 min later; a run missed while the host was off runs at boot) | `s3://<ops bucket>/backups/<UTC date>/classora.dump` and `counts.json`, SSE-S3 | 14 days (bucket lifecycle) |
| Data Lifecycle Manager policy `classora-<env>-data` volume | 09:00 UTC | EBS snapshots of the data volume, tagged with the volume's tags | 7 |

- `backup.sh` runs `pg_dump --format=custom` inside the `db` container and streams it to S3. `counts.json` holds the exact row count of every table, taken in the same snapshot as the dump, so a restore must match it exactly.
- A failed dump writes no object: the stream is cut before it ends, so S3 never completes the upload, and an earlier dump under the same key stays as it was.
- Each run logs one JSON line to the journal and to `/var/log/classora/backup.log` (rotated weekly, 8 kept), for example `{"event": "backup", "env": "dev", "status": "ok", "bytes": 725056, "duration_s": 3, "bucket": "…", "key": "backups/2026-11-12/classora.dump", "tables": 139}`. A failure has `"status": "failed"` and an `error`, and the unit fails. Alerts on these lines arrive with M9-09.
- No password appears on a command line, in the logs or in S3 metadata: `psql` and `pg_dump` use the container's local socket.
- The snapshot policy targets the data volume by its `Name` tag (`classora-<env>-data`): Data Lifecycle Manager selects a volume that has **any** of the target tags, so `Project`/`Environment`/`Role` would also select other volumes.
- RPO up to 24 hours; RTO about 30 minutes for a small database (final values: M10-04, #152).

Check them:

- `systemctl list-timers classora-backup.timer` (next run), `journalctl -u classora-backup`, `tail /var/log/classora/backup.log`.
- `aws s3 ls s3://<ops bucket>/backups/ --recursive` (the bucket is the `ops_bucket` output of `envs/<env>`).
- `aws ec2 describe-snapshots --owner-ids self --filters Name=tag:Name,Values=classora-dev-data`.
- A backup now: `systemctl start classora-backup.service` (the same as the nightly run; it overwrites today's dump).

## Restore drill (dump)

On the host:

```bash
/opt/classora/bin/restore.sh latest                      # or backups/<date>/classora.dump
/opt/classora/bin/restore.sh --self-test latest          # must FAIL: proves the comparison works
```

- It downloads the dump and its `counts.json`, starts a scratch container `classora-restore` from the live `db` image (the host's pinned `postgres:18.x`, ADR-0001 A2) (its own internal network, no published port, 384 MB, a scratch directory on the data volume), creates the `classora_app`/`classora_monitor` roles without login, runs `pg_restore --no-owner --exit-on-error`, counts every table and prints `source / restored / match` per table, then one JSON verdict (`"event": "restore"`, also in `backup.log`).
- Exit 0: every table matches. 1: a mismatch. 2: usage. 3: the restore failed.
- `--self-test` deletes one row in the scratch copy before comparing, so it must exit 1.
- The scratch container, network and directory are always removed. It never connects to the live `db` and refuses a container named `classora-restore` that isn't its own.
- Check the live database was untouched: `docker ps` before and after shows the same `classora-db-1` uptime.
- It takes about 10 seconds on DEV (2026-11-12: 139 tables, all match).

## Restore drill (snapshot)

From your own machine, in the repository:

```bash
infrastructure/scripts/snapshot-drill.sh dev                         # newest DLM snapshot
infrastructure/scripts/snapshot-drill.sh dev --snapshot snap-0123…   # a given one
```

1. It picks the newest completed snapshot of `classora-dev-data` made by the policy.
2. It creates a gp3 volume from it in the host's zone (`Purpose=drill`) and attaches it as `/dev/sdg`.
3. Over Run Command it runs `restore.sh --from-device /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol…` on the host. That mounts the drill volume read-only (XFS replays its log onto the drill copy; the snapshot and the live volume are never written), copies the PostgreSQL directory into the scratch directory, starts the scratch container on the copy (PostgreSQL crash recovery, as after a power loss) and prints the row counts next to the latest dump's.
4. It always detaches and deletes the drill volume, then prints how many `Purpose=drill` volumes are left (expect 0). Check again with `aws ec2 describe-volumes --filters Name=tag:Purpose,Values=drill`.

Differences from the dump are writes made between 08:30 and the snapshot, mostly Procrastinate's queue tables; they are reported, not failed. Record one known table's count (for example `public.alembic_version` = 1, `public.permissions`). A volume costs cents for the few minutes it exists.

## Real restore into the live database

Restoring replaces every change since the dump. Downtime is about 15–30 minutes on a small database. Do a dump drill of the same dump first.

**Never reverse migrations.** Restore a dump only into a release whose schema is the same or newer than the dump's. After the restore, `alembic upgrade head` (step 7) brings it to the release's schema. Never deploy an older release onto a newer dump.

1. Pick the dump (`aws s3 ls s3://<ops bucket>/backups/`) and drill it: `restore.sh backups/<date>/classora.dump`.
2. Stop the app, keep `db`: `systemctl stop classora-app`, then `compose.sh stop pgbouncer`.
3. Download it on the host, never to another machine: `aws s3 cp s3://<ops bucket>/backups/<date>/classora.dump /root/classora.dump`.
4. Restore into a fresh database:

   ```bash
   C=/opt/classora/bin/compose.sh
   $C exec -T db sh -c 'createdb -U "$POSTGRES_USER" classora_new'
   $C exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d classora_new --no-owner --exit-on-error' < /root/classora.dump
   ```

5. Swap the databases and restore the database-level settings of migration 0096 and `db-roles.sh` (they belong to the database, not the dump):

   ```bash
   $C exec -T db sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres' <<'SQL'
   ALTER DATABASE classora RENAME TO classora_old;
   ALTER DATABASE classora_new RENAME TO classora;
   GRANT CONNECT ON DATABASE classora TO classora_app, classora_monitor;
   REVOKE TEMPORARY ON DATABASE classora FROM PUBLIC;
   SQL
   ```

6. `rm /root/classora.dump`.
7. `/opt/classora/bin/manage.sh migrate` (`alembic upgrade head`, the runtime role's password, `db-roles.sh`).
8. `systemctl start classora-data classora-app`, then check the site and `/health/ready` (see [`host.md`](host.md)).
9. After a day, drop the old database: `$C exec -T db sh -c 'dropdb -U "$POSTGRES_USER" classora_old'`.

A whole-volume restore (a volume from a snapshot in place of the data volume) is faster for a large database but loses everything after 09:00 of that day and is crash-consistent; use the dump unless the data volume itself is lost.

## Final dump at shutdown

Before an environment that holds real data (PROD) is destroyed, on its host:

```bash
/opt/classora/bin/final-dump.sh
```

1. `backup.sh --archive` writes `classora.dump` and `counts.json` to `s3://classora-archive-<suffix>/<prefix>/<UTC timestamp>/`. The bucket and prefix are the parameters `/classora/<env>/archive_bucket` and `archive_prefix`: `prod` for PROD, the drill-only `dev-drill` for DEV. The host may only put and get objects under its own prefix.
2. `restore.sh` restores that archive object into the scratch container and compares the counts.
3. Only if every table matches, it writes `verified.json` (dump, time, tables) next to them, and logs `{"event": "final-dump", "status": "verified", …}`.

The archive is versioned, encrypted and kept 365 days (Glacier after 30). `make tf-destroy ENV=prod` refuses without a verified final dump newer than PROD's last deploy ([`teardown.md`](teardown.md)). DEV is destroyed without one; its `dev-drill/` objects come only from drills and are deleted afterwards (noncurrent versions expire after 30 days).

No dump or archive object is copied to DEV, a local machine or CI: restores happen on the environment's own host.
