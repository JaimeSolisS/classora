# Runbook: secrets and rotation

Where each secret lives and how to rotate one between environment re-creates. The decisions are in [ADR-0012](../adr/0012-secrets-and-configuration-delivery.md): Terraform generates every secret per environment into Parameter Store (`SecureString`, AWS-managed `aws/ssm` key); the hosts render them into a root-only env file (`render-env.sh`, `render-monitoring-env.sh`); GitHub holds no secret (OIDC, variables only). **Re-creating an environment rotates all of its secrets.** Nothing rotates automatically.

Every command from your own machine uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`. Host commands run as root over Session Manager ([`host.md`](host.md)). Never print a value in a shared terminal, a log or an issue.

## Inventory

| Parameter | Used by | Rotate |
|---|---|---|
| `/classora/<env>/postgres_password` | `db` (the owner role `classora`), `migrate`, `manage.sh` | [PostgreSQL owner](#postgresql-owner-password) |
| `/classora/<env>/app_db_password` | `api`, `worker`, `pgbouncer` (runtime role `classora_app`, #165) | [Runtime and monitor roles](#runtime-and-monitor-role-passwords) |
| `/classora/<env>/monitor_db_password` | the collector's PostgreSQL receiver (`classora_monitor`) | same |
| `/classora/monitoring/grafana_admin_password` | Grafana `admin` (colocated: `/classora/dev/monitoring/…`) | [Grafana](#grafana-admin-password) |
| `/classora/monitoring/smtp_username`, `smtp_password` | Grafana's SES SMTP (IAM user `classora-ses-smtp`) | [SES SMTP key](#ses-smtp-key) |
| `/classora/prod/owner_initial_password` | the PROD Owner's first sign-in only, not Terraform-managed | delete it after the first sign-in ([`release.md`](release.md#first-release-and-the-prod-owner)) |

Each host role reads only its own `/classora/<env>/` path (and `/classora/shared/`): an explicit deny stops the DEV, PROD and monitoring hosts from reading each other's paths, although `AmazonSSMManagedInstanceCore` allows `ssm:GetParameter` on every parameter (#618). Terraform state holds the generated values; the state bucket is readable only by the owner (ADR-0012).

## Preconditions and downtime

- A rotation is a plan and apply of the environment's root with `-replace` (owner-run; free), then a redeploy of the **current** release. Check the plan replaces only the named `random_password` (or key) and updates the parameter in place.
- The redeploy recreates the containers whose env changed: a few seconds of API errors; the SPA keeps serving. Rotate outside business hours on PROD.
- Find the current release on the host: `basename "$(readlink -f /srv/classora-data/releases/current)"`.

## Runtime and monitor role passwords

1. `make tf-plan ENV=<env> ARGS='-replace=module.app_host.random_password.db["app_db_password"]'` (or `monitor_db_password`), check, `make tf-apply ENV=<env>`.
2. Redeploy the current release: on the host `/opt/classora/bin/deploy.sh <current sha>`, or the workflow (`deploy-dev.yml` with `-f sha=<sha>`; PROD: `release-prod.yml` with `-f sha=<sha>` and the approval). `deploy.sh` renders the new env file, sets `classora_app`'s password (`sync-runtime-role`) and `classora_monitor`'s (`db-roles.sh`) in the migrate step, then recreates `api`, `worker`, `pgbouncer` and the collector with it.
3. Verify: `deploy.sh` exits 0; on the host `compose.sh exec -T db psql -U classora -d classora -Atc "select usename, count(*) from pg_stat_activity where datname='classora' group by 1"` shows `classora_app` connections.

## PostgreSQL owner password

PostgreSQL reads `POSTGRES_PASSWORD` only when it creates a new data directory, so the role's password is changed by hand **before** the redeploy:

1. `make tf-plan ENV=<env> ARGS='-replace=module.app_host.random_password.db["postgres_password"]'`, check, `make tf-apply ENV=<env>`.
2. On the host, render the new env file and set the new password on the role through the container's local socket, without putting it on a command line or in the server log:

   ```bash
   /opt/classora/bin/render-env.sh
   NEW_PGPASS=$(sed -n 's/^POSTGRES_PASSWORD=//p' /opt/classora/env/app.env) \
     /opt/classora/bin/compose.sh exec -T -e NEW_PGPASS db psql -X -v ON_ERROR_STOP=1 -U classora -d postgres <<'SQL'
   SET log_statement = 'none';
   \getenv pw NEW_PGPASS
   ALTER ROLE classora PASSWORD :'pw';
   SQL
   ```

3. Redeploy the current release (as above). `db` and `migrate` get the new value; `deploy.sh`'s migrate step proves the owner can connect with it.
4. Verify: `deploy.sh` exits 0, and `systemctl start classora-backup.service` then `tail -n1 /var/log/classora/backup.log` shows `"status": "ok"`.

If step 3 fails with an authentication error, step 2 didn't apply: repeat it (the env file already holds the new value).

## Grafana admin password

Grafana sets the admin password only on its first start.

1. `make tf-plan ENV=monitoring ARGS='-replace=module.monitoring_host.module.config.random_password.grafana_admin'` (with `TF_VAR_alert_email`), check, `make tf-apply ENV=monitoring`.
2. `make monitoring-sync` (it renders `/opt/classora/env/monitoring.env`), then on the monitoring host set the new value without a command-line argument:

   ```bash
   sed -n 's/^GRAFANA_ADMIN_PASSWORD=//p' /opt/classora/env/monitoring.env |
     /opt/classora/bin/monitoring-compose.sh exec -T grafana grafana cli admin reset-admin-password --password-from-stdin
   ```

   Colocated on DEV, run the same on DEV after a DEV redeploy ([`monitoring.md`](monitoring.md#grafana-login)).
3. Verify: sign in at `https://grafana.classora.dev` with the new value.

## SES SMTP key

1. `make tf-plan ENV=monitoring ARGS='-replace=module.monitoring_host.module.config.aws_iam_access_key.ses_smtp'`, check (the key and the two parameters), `make tf-apply ENV=monitoring`.
2. `make monitoring-sync` (Grafana is recreated with the new credentials).
3. Verify: Grafana → Alerting → Contact points → `owner-email` → **Test** delivers an e-mail.

## After a suspected leak

Rotate the affected secret now, as above. For the owner's AWS user or the state bucket, rotate the IAM user's access key in the console first, then re-create the environments (`make env-teardown`, then apply again) so every generated secret changes.
