# Runbook: DEV data (English simulated center and the visitor login)

DEV (<https://dev.classora.dev>) runs on the English simulated tutoring center, plus a public Owner login for visitors and graders. Every night at 04:00 UTC a reset wipes DEV's `classora` database and restores that data. PROD never gets any of it. The decision is the ADR-0018 amendment "DEV data (#671)" ([ADR-0018](../adr/0018-sizing-load-and-recovery-objectives.md)); the issues are #671 and #674.

Every AWS command below uses the owner's profile (`export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`). Host commands run over Session Manager ([`host.md`](host.md#reach-the-host-session-manager-no-ssh)).

## What DEV holds

- The English simulated center: `python -m tests.factories.simulation --language en --seed 1`, the same data as `make seed-data-english` locally (term from 1 Sep to 5 Dec 2026, history to 18 Nov).
- `visitor` / `visitor-classora`: display name "Demo visitor", role Owner only, active, language English, current privacy notice accepted. The root README lists it.
- Every other simulated account works on DEV with its **published password**: `owner` / `classora-sim-owner`, `admin` / `classora-sim-admin`, and the rest in the [full README](../Full_README.md#simulated-operations-data). The passwords aren't scrubbed (owner decision, #671).
- The visitor and those accounts can do everything their role allows, so anyone can change or delete data on DEV until the next reset. The app sends no product e-mail (ADR-0007 D11), so they can't send mail.
- The Owner account that existed on DEV before the first reset is gone. It is kept only in the pre-wipe backup (below). Use `owner` or `visitor` instead.

## How the data is built

The `dev-data` job of `deploy-dev.yml` ([`deploy.md`](deploy.md#the-automatic-flow)) runs for every DEV deploy:

1. On a throwaway PostgreSQL on the runner (the host's exact `postgres` pin, checked against `compose.aws.yaml`), with `APP_ENV=dev`: `alembic upgrade head`, `python -m app.seeds`, the English simulation, then `python -m tests.factories.simulation.visitor`.
2. `pg_dump -Fc --no-owner --no-acl` and exact per-table row counts (the backup's query, `backup-common.sh`).
3. Uploads `releases/<sha>.dev-data.dump` and `releases/<sha>.dev-data.counts.json` to the DEV ops bucket. The `releases/` prefix expires after 30 days.

`deploy` needs this job, so a broken simulation stops the DEV deploy (the previous release keeps serving). A commit from before #674 (a rollback) builds nothing: the reset then uses its newest cached dump.

The simulation command and the visitor step refuse any `APP_ENV` but `local` and `dev` before connecting, and `release-prod.yml` never references these objects.

## The nightly reset

`classora-dev-data-reset.timer` starts `classora-dev-data-reset.service` at 04:00 UTC (within 5 minutes; a run missed while the host was off happens at the next boot). The service runs `/opt/classora/bin/dev-data-reset.sh`:

1. Refuses (exit 2) unless `/etc/classora/host.env` says `CLASSORA_ENV=dev`, and unless `/srv/classora-data/dev-data/ENABLED` exists.
2. Waits up to 15 minutes for the backup lock, then the deploy lock, so it never overlaps a backup or a deploy.
3. Picks the dump of the current release (`readlink /srv/classora-data/releases/current`): cached in `/srv/classora-data/dev-data/`, else downloaded from the ops bucket. If neither works, the newest cached dump, which step 6's migrations bring to the current head (`"fallback": true`). With none it fails before stopping anything.
4. Stops `api`, `worker` and `pgbouncer`.
5. Drops and re-creates `classora`, runs `pg_restore --no-owner --exit-on-error`, then counts every table and compares with the dump's counts.
6. Gives the runtime role the grants migration 0096 gives (the dump has no ACL), then runs the deploy's database step in a one-off `migrate` container: `alembic upgrade head`, `sync-runtime-role`, the reference seeds. Then `db-roles.sh`.
7. Starts every service and runs the deploy's 90 s readiness gate (`api`, `worker`, the site's `/` through Caddy).
8. Keeps the three newest cached dumps.

If anything fails after step 4, every service is started again anyway. The units are installed and enabled only on DEV (`install-units.sh`, run by every deploy); on any other host they're removed if present.

The reset takes a few minutes. During it, the site loads but its API calls fail.

## Run it by hand

```bash
sudo /opt/classora/bin/manage.sh reset-dev-data
```

It starts the same unit (so the same locks, refusal and log), waits for it and prints its JSON line. It exits 2 on any host but DEV and 3 when the `ENABLED` marker is missing.

## Dry run

```bash
sudo /opt/classora/bin/dev-data-reset.sh --check
```

Prints the environment, the marker, the current release, which dump a reset would use (cached, download or fallback) and the plan. It changes nothing: no lock, no container, no download. Exit 0 when a reset could run, 1 when there is no release or no dump, 2 when refused.

## Reading the result

Each run writes one JSON line to the journal and to `/var/log/classora/dev-data-reset.log`:

```json
{"event": "dev-data-reset", "env": "dev", "status": "ok", "release": "<sha>", "dump": "<sha>", "fallback": false, "tables": 120, "mismatched": 0, "duration_s": 75}
```

- `status`: `ok`; `mismatch` (restored, started and healthy, but `mismatched` tables differ from the dump's counts); `failed` (with an `error`).
- `release`: the release running; `dump`: the release whose dump was restored (different only with `"fallback": true`).
- On the host: `journalctl -u classora-dev-data-reset -o cat | tail -n 5` or `tail -n 5 /var/log/classora/dev-data-reset.log`.
- In Grafana (Explore, Loki): `{service_name="dev-data-reset"} | json`. The collector ships every `/var/log/classora/*.log` with the file name as the service name.
- Timer state: `systemctl list-timers classora-dev-data-reset.timer`. A run skipped because the marker is missing shows as a failed condition in `systemctl status classora-dev-data-reset.service`.

No alert covers the reset (owner decision, #671): check the journal or Loki.

## Before the first wipe

The first reset destroys DEV's database as it was. Do these steps in order on the DEV host and record each result on #671. Only then create the marker.

1. Take a backup and check its result line says `"status": "ok"`:
   ```bash
   sudo systemctl start classora-backup.service
   journalctl -u classora-backup -o cat | grep '^{"event": "backup"' | tail -n 1
   ```
2. Drill it, and check the line says `"status": "ok"`:
   ```bash
   sudo /opt/classora/bin/restore.sh backups/$(date -u +%F)/classora.dump
   ```
3. Copy that dump and its counts to the archive bucket's DEV drill prefix:
   ```bash
   sudo bash -c '. /etc/classora/host.env
     archive=$(aws ssm get-parameter --region "$AWS_REGION" --name /classora/dev/archive_bucket --query Parameter.Value --output text)
     day=$(date -u +%F); dest="s3://$archive/dev-drill/pre-671-$(date -u +%Y%m%d)"
     for f in classora.dump counts.json; do
       aws s3 cp --only-show-errors --region "$AWS_REGION" --sse AES256 "s3://$OPS_BUCKET/backups/$day/$f" "$dest/$f"
     done
     echo "$dest"'
   ```
   Optionally drill the copy: `sudo /opt/classora/bin/restore.sh s3://<archive>/dev-drill/pre-671-<YYYYMMDD>/classora.dump`.
4. Record both JSON lines and the archive keys on #671.
5. Create the marker and note its time:
   ```bash
   sudo install -d -m 0700 /srv/classora-data/dev-data
   sudo touch /srv/classora-data/dev-data/ENABLED
   sudo stat -c '%y' /srv/classora-data/dev-data/ENABLED
   ```
6. Run the first reset: `sudo /opt/classora/bin/manage.sh reset-dev-data`. Check `"status": "ok"`, `"mismatched": 0` and `release` equal to the deployed commit, then sign in as `visitor`.

The `dev-drill/` archive objects are kept 365 days, like every DEV drill ([`restore.md`](restore.md)).

## Turning it off

- Pause the reset: `sudo rm /srv/classora-data/dev-data/ENABLED`. The timer keeps firing, but the unit is skipped and nothing is wiped.
- DEV is shut down a few days after grading ([`teardown.md`](teardown.md)). Nothing here needs undoing first.

## Troubleshooting

| Symptom | Meaning | What to do |
|---|---|---|
| `refused: this host is '…', not dev` (exit 2) | Not DEV, or `host.env` has no `CLASSORA_ENV`. | Nothing; the reset never runs elsewhere. |
| `ENABLED is missing` (exit 2 or 3) | The pre-wipe steps aren't done, or the reset is paused. | Do [Before the first wipe](#before-the-first-wipe), or leave it paused. |
| `failed`, `no dump for <sha> and none cached` | The `dev-data` job didn't upload (or the objects expired) and nothing is cached. | Rerun `deploy-dev` for the current commit (`gh workflow run deploy-dev.yml --ref develop`), then reset by hand. |
| `failed`, `the backup lock was held…` or `the deploy lock…` | A backup or deploy ran for over 15 minutes. | Check `journalctl -u classora-backup` or the deploy run; reset by hand afterwards. |
| `failed`, `not ready after 90 s` | The data is restored but a service isn't healthy. | `compose.sh ps`, `compose.sh logs --tail 200 api worker edge`; a redeploy (`deploy.sh <sha>`) restarts everything. |
| `mismatch` | The restore and the dump's counts disagree. | Run `--check`, then a manual reset; if it repeats, compare the counts file with the table counts printed in the journal. |
