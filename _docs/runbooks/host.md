# Runbook: app hosts (DEV, later PROD)

How to reach, operate, replace and destroy a Classora app host. The decisions behind it are in [ADR-0010](../adr/0010-hosting-edge-and-https.md), [ADR-0011](../adr/0011-infrastructure-as-code-images-and-release.md) and [ADR-0012](../adr/0012-secrets-and-configuration-delivery.md). Terraform basics (backend, `make tf-*`) are in [`terraform.md`](terraform.md).

Every command below uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`.

## Create

1. `make tf-plan ENV=dev`, read the plan, then `make tf-apply ENV=dev` (type `dev`). It's billable: about US$18/month.
2. Cloud-init then needs about 2 minutes. It installs Docker and Compose, adds the swap file, mounts the data volume and enables the units. Check it with `cloud-init status` on the host: it should say `done`. Nothing runs until the first release.
3. Do the first release (below), then bootstrap the Owner (below).

## Reach the host (Session Manager, no SSH)

- Install the plugin once: `brew install --cask session-manager-plugin`.
- `aws ssm start-session --target "$(aws ssm get-parameter --name /classora/dev/instance_id --query Parameter.Value --output text)"`.
- The session starts as `ssm-user`. Use `sudo -i` for the commands below; all of them need root.

## Layout

| Path | What |
|---|---|
| `/etc/classora/host.env` | Environment, region, ops bucket and data volume ID, from user data. No secrets. |
| `/opt/classora/bin/` | Host scripts: `deploy.sh`, `manage.sh`, `compose.sh`, `render-env.sh`, `db-roles.sh`, `ecr-login.sh`, `mount-data-volume.sh`, `host-setup.sh`. Each deploy refreshes them from the release. |
| `/opt/classora/env/app.env` | Env file rendered from Parameter Store. Mode `0600`, owner root. Never print it. |
| `/srv/classora-data/` | The data volume (XFS, label `classora`): `postgres/`, `caddy/` (certificates), `collector/`, `releases/<sha>/` with the `current` and `previous` links. |

## Services and logs

- Units:
  - `classora-data.service`: `db`, then `pgbouncer`.
  - `classora-app.service`: `api`, `worker`, `edge` and, with telemetry on, `collector`. It requires the data unit and starts after it.
  - Both are oneshot units that run `compose.sh up -d` and `compose.sh stop`.
  - Check them with `systemctl status classora-data classora-app`.
- Compose: run `/opt/classora/bin/compose.sh <args>` for the current release, for example `compose.sh ps` or `compose.sh logs --tail 100 api`.
- Docker rotates logs (`json-file`, 10 MB × 3 per container). Each line carries the Compose service name.
- Unit and deploy output: `journalctl -u classora-data -u classora-app`.
- Health isn't public: `https://<host>/health/*` returns 404. From the host, run `compose.sh exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read())"`.

## First release (until M9-07 automates it)

1. Set the GitHub `dev` environment variables (no secrets):
   - `gh variable set AWS_ROLE_ARN --env dev --body <bootstrap output gha_role_arns.dev>`;
   - `gh variable set OPS_BUCKET --env dev --body <envs/dev output ops_bucket>`.
2. Run the `deploy-dev` workflow on the commit to release: `gh workflow run deploy-dev.yml --ref <branch>`. GitHub can only dispatch a workflow that exists on the default branch. It:
   - builds and checks the arm64 images;
   - pushes them as `sha-<commit>`;
   - uploads `releases/<commit>.tgz`.
3. On the host, run `sudo /opt/classora/bin/deploy.sh <40-hex commit>`. It:
   - downloads the bundle and installs its host scripts and units;
   - renders the env file;
   - pulls the images by digest;
   - starts `db`;
   - runs `alembic upgrade head`, `sync-runtime-role` and the reference seeds as the owner, then `db-roles.sh`;
   - switches `current`, starts everything and checks `/health/ready` and the edge for 90 s.

   If the new release isn't healthy, it rolls back to `previous` and exits 1. Migrations are never reversed.

## Owner account

- Create the first Owner: `sudo /opt/classora/bin/manage.sh bootstrap-owner <username>`. It prompts for the password twice and is refused once an Owner exists.
- Locked-out Owner: `sudo /opt/classora/bin/manage.sh reset-owner-password <username>`. It prints a one-time link that expires in 24 hours.
- Migrate only: `sudo /opt/classora/bin/manage.sh migrate`.

DEV gets the reference seeds and an Owner only. It never gets demo or simulated data.

## Certificates

- Caddy keeps its ACME account and certificates in `/srv/classora-data/caddy`, so reboots, redeploys and instance replacements keep the same certificate.
- Let's Encrypt allows about 5 duplicate certificates per hostname per week. Only a destroyed data volume forces a new one.
- `acme_ca` is a Terraform variable. Use `staging` only for automated tests: browsers reject staging certificates on `.dev`.
- Before the first release you can obtain the certificate with the edge's base image, as on 2026-11-11 (#614). Run `caddy:2.11.4` pinned by digest as uid 10002, with `CAP_NET_BIND_SERVICE`, `/srv/classora-data/caddy:/data` and a two-line Caddyfile (`dev.classora.dev { file_server }`). Remove it before the first deploy, because the edge needs ports 80 and 443.

## Reboot

`sudo reboot` needs no manual step:
1. Docker waits for the data volume mount.
2. `classora-data` renders the env file, logs in to ECR and starts `db` and `pgbouncer`.
3. `classora-app` starts the rest and waits for health.

Containers were stopped by the units at shutdown, so the restart policy doesn't race the units.

## Replace the instance

`make tf-plan ENV=dev ARGS=-replace=module.app_host.aws_instance.this`, check that the plan replaces only the instance and the volume attachment, then `make tf-apply ENV=dev`. Terraform then:
1. stops the old instance and detaches the data volume;
2. terminates it;
3. launches a new one on the same network interface and Elastic IP;
4. reattaches the volume.

Cloud-init then mounts the volume **without formatting it** (it has a filesystem), finds `releases/current` and starts it. The data and the certificate are unchanged. The AMI is read at creation, so a replacement also picks up the latest AL2023.

## Resize the data volume

1. Raise `data_gb` (module input) and apply. EBS grows the volume online.
2. On the host, run `sudo xfs_growfs /srv/classora-data`.

You can't shrink a volume.

## Secrets

- To read a value: `aws ssm get-parameter --name /classora/dev/<name> --with-decryption`. It's never needed for normal operation.
- To rotate one, run `terraform apply -replace='module.app_host.random_password.db["<name>"]'`, then:
  - `app_db_password` and `monitor_db_password`: redeploy. `deploy.sh` runs `sync-runtime-role` and `db-roles.sh`.
  - `postgres_password`: first run `ALTER ROLE classora PASSWORD …` inside `db`, then redeploy. PostgreSQL reads `POSTGRES_PASSWORD` only on a new volume.

## Destroy DEV

`make tf-destroy ENV=dev` and type `dev`. This removes:
- the instance;
- the data volume, with all DEV data and the certificate;
- the Elastic IP and the network interface;
- the security group and the role;
- the ops bucket, with its releases and backups;
- the parameters and the A record.

Nothing in `shared` or `bootstrap` changes. A later re-create gets new secrets and a new certificate, which counts toward the Let's Encrypt limit above.
