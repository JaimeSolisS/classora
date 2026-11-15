# Runbook: monitoring (Grafana, alerts, telemetry)

How the monitoring stack runs on AWS, how to log in, switch where it runs, change alerts and destroy it. The decisions are in [ADR-0014](../adr/0014-observability-error-tracking-and-alerting.md) and [ADR-0012](../adr/0012-secrets-and-configuration-delivery.md). The app hosts are in [`host.md`](host.md); Terraform basics in [`terraform.md`](terraform.md). The local profile (`make dev-observability`) is in the [full README](../Full_README.md).

Every command uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`.

## What runs where

- **App hosts (DEV and PROD):** the collector agent (`collector`, Compose profile `telemetry`) runs only while `/classora/shared/otlp_endpoint` exists. It ships host metrics, container stats and logs, `/var/log/classora/*.log` (the backup results), PostgreSQL and job metrics, readiness checks, and the API's and worker's OTLP. Without the parameter the agent doesn't start and the app exports nothing.
- **The stack:** the gateway collector, Prometheus, Loki, Tempo and Grafana (Compose project `monitoring`), from the same files as the local profile (`infrastructure/observability/`), plus `infrastructure/host/monitoring/compose.monitoring.yaml`. Stores on the encrypted data volume under `/srv/classora-data/monitoring/`. Retention: metrics 15 days, logs and traces 7 days.
- **Grafana:** `https://grafana.classora.dev`, through Caddy. Login is required; anonymous access and sign-up are off, and Grafana locks a user for 5 minutes after 5 failed logins.
- **Alerts:** Grafana-managed rules (`grafana/provisioning/alerting/rules.yaml`) e-mail the owner through Amazon SES, from `alerts@classora.dev` (contact point `owner-email`, `infrastructure/host/monitoring/notifications.yaml`; repeat every 4 hours while firing, then a "resolved" e-mail).

## Modes (`monitoring_mode` in `envs/dev`)

| Mode | Where the stack runs | Cost |
|---|---|---|
| `dedicated` (default) | Its own t4g.small host, root `envs/monitoring` | about US$19/month |
| `colocated` | On DEV, which becomes a t4g.medium with a 30 GB data volume | about US$13/month more on DEV |
| `off` | Nowhere: no Grafana record, no agent | 0 |

Each root refuses to plan while the other mode's resources exist, because both own the same names (the `grafana` record, `classora-ses-smtp`, `/classora/shared/otlp_endpoint`).

### Create (dedicated)

1. `export TF_VAR_alert_email=<the owner's address>` (a verified SES identity, see below; never committed).
2. `make tf-plan ENV=monitoring`, read it, then `make tf-apply ENV=monitoring` (type `monitoring`).
3. Wait about 2 minutes for cloud-init (Docker, Compose, the data volume).
4. `make monitoring-sync`: bundles `infrastructure/observability` and `infrastructure/host` from HEAD (committed files only), uploads it, and runs `deploy-monitoring.sh` on the host through the SSM document `classora-monitoring-sync`. Caddy gets the certificate on its first start.
5. Apply the agents: the next DEV deploy, or `sudo /opt/classora/bin/deploy.sh <current sha>` on DEV, renders the endpoint and starts the agent. Data appears within a minute.
6. When DEV is re-created (new security group), re-apply `envs/monitoring` so the 4318 rule admits the new group.

Update the dedicated stack (configs, dashboards, rules) with `make monitoring-sync` after merging. CI doesn't touch it.

### Switch to colocated

1. `make tf-destroy ENV=monitoring` (type `monitoring`). Its telemetry is lost.
2. `make tf-plan ENV=dev ARGS='-var=monitoring_mode=colocated'` with `TF_VAR_alert_email` set. Expect the instance type and data volume size to change in place (DEV stops for 1–2 minutes), the `/classora/dev/monitoring/*` parameters, the SMTP user, the shared endpoint and the `grafana` record. Nothing may be destroyed or replaced. Apply it.
3. Deploy DEV (`deploy.sh` sees `MONITORING_MODE=colocated`, starts the stack on DEV's app network and adds `grafana.caddy` to the edge). `mount-data-volume.sh` grows the filesystem after the volume resize at the next boot or deploy.
4. To keep the mode, set `monitoring_mode = "colocated"` in a git-ignored `*.tfvars` or always pass the `-var`.

Back to dedicated: plan and apply `envs/dev` with `monitoring_mode=dedicated` (DEV shrinks back to t4g.small; the data volume stays 30 GB because EBS can't shrink), deploy DEV (`deploy.sh` stops the colocated stack and removes the Grafana site), then create `envs/monitoring` as above.

### Off

Destroy `envs/monitoring` (or leave colocated as above), then plan `envs/dev` with `monitoring_mode=off`. Redeploy DEV: without the endpoint parameter the agent isn't started.

## Grafana login

- User `admin`. Password: `aws ssm get-parameter --with-decryption --name /classora/monitoring/grafana_admin_password --query Parameter.Value --output text` (colocated: `/classora/dev/monitoring/grafana_admin_password`).
- Grafana sets it only on its first start. Changing the parameter later changes nothing; reset it in the UI or with `monitoring-compose.sh exec grafana grafana cli admin reset-admin-password`.
- Locked out after failed attempts: wait 5 minutes.

## The monitoring host

- Reach it: `aws ssm start-session --target "$(aws ssm get-parameter --name /classora/monitoring/instance_id --query Parameter.Value --output text)"`, then `sudo -i`.
- `/opt/classora/bin/monitoring-compose.sh ps|logs <service>`; `systemctl status classora-monitoring`.
- `/opt/classora/env/monitoring.env` is rendered from `/classora/monitoring/*` (mode 0600). Never print it.
- Bundles: `/srv/classora-data/monitoring/releases/<sha>` and `current`.
- The gateway listens on the private IP's 4318 only; its security group admits it only from the app hosts' groups.

## Alerts

| Rule | Fires when |
|---|---|
| Host CPU above 90% | for 10 minutes |
| Available memory below 10% | for 10 minutes |
| Disk above 85% | any mounted filesystem, for 5 minutes |
| API readiness failing | `/health/ready` not 2xx for about 2 minutes (worst case 3 minutes to fire) |
| Background job failed in the last hour | any |
| Background job doing for over 30 minutes | any |
| Backup failed | a `"status": "failed"` backup line in the last hour |
| No successful backup in 26 hours | no `"status": "ok"` backup line in 26 hours, for an environment whose API or worker logged in the last hour |

- **Change a threshold:** edit `rules.yaml`, run `make check-observability`, merge, then `make monitoring-sync` (dedicated) or deploy DEV (colocated). Provisioned rules can't be edited in the UI.
- **Mute:** in Grafana, Alerting > Silences > New silence (matchers such as `alertname` and `deployment_environment_name`, and a duration). A silence lives in Grafana's database and is lost with the host.
- **E-mail content:** the rule, its labels (environment, host name, mount point), the summary, a link to the rule and to its dashboard panel. Nothing from the application's data.
- **SES sandbox:** the account can send only to verified addresses, which is enough for the owner. While the recipient identity is `PENDING`, Grafana logs `554 Message rejected: Email address is not verified`. Check with `aws sesv2 get-email-identity --email-identity <address> --query VerifiedForSendingStatus`; re-send the verification mail with `aws ses verify-email-identity --email-address <address>`. A test e-mail: Alerting > Contact points > owner-email > Test.

## Destroy

- `make tf-destroy ENV=monitoring` (type `monitoring`): the host, both volumes (all telemetry, Grafana's database and silences, the certificate), the EIP, the record, the security group, the role, the ops bucket, the SSM document, the SMTP user and key, and every `/classora/monitoring/*` parameter and `/classora/shared/otlp_endpoint`.
- The agents on the app hosts then fail to export until their next deploy, which renders no endpoint and stops them. Redeploy DEV to stop it at once.
- A re-create gets a new Grafana password, a new SMTP key and a new certificate (Let's Encrypt allows about 5 per week for the hostname).
