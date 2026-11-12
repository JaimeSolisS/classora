# Runbook: teardown at the end of the window

The environments live about 20 days (owner direction, M9 plan). At the window's end the owner chooses to keep DEV only or to remove everything but `bootstrap`. PROD's data goes to the archive first, as a verified final dump (ADR-0013, decision 8). The decisions are in [ADR-0011](../adr/0011-infrastructure-as-code-images-and-release.md) ("Destroy is a normal operation") and [ADR-0013](../adr/0013-backups-restore-drills-and-shutdown-data.md).

Every command uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`.

## Preconditions

- The owner decided the mode and the date. Users of PROD were told: writes after the final dump are lost.
- No release is running (`gh run list --workflow release-prod.yml --limit 1`), and no deploy to DEV for `all`.
- The local `infrastructure/terraform/backend.hcl` exists ([`terraform.md`](terraform.md)).
- `TF_VAR_alert_email` is exported, or `/classora/monitoring/alert_email` still exists (the script reads it).

## Modes

| | `make env-teardown MODE=dev-only` | `make env-teardown MODE=all` |
|---|---|---|
| PROD | final dump, then destroyed | final dump, then destroyed |
| Monitoring host | destroyed | destroyed |
| DEV | kept, `monitoring_mode=off` (`MONITORING=colocated`: the stack moves onto DEV, t4g.medium) | destroyed |
| `shared` (VPC, ECR and its images) | kept | destroyed |
| `bootstrap` (zone, state, archive, budget, SES, OIDC roles) | kept | kept |
| About per month afterwards | US$19 (DEV US$18, zone and storage); colocated about US$31 | US$0.55 (zone US$0.50, state and archive storage) |

`bootstrap` is never torn down: the archive bucket holds the final dump for 365 days (Glacier after 30), and the zone holds the registrar's records.

## Steps

`make env-teardown MODE=dev-only|all` runs these in order. Each step asks for `yes` (anything else skips it), and each destroy also asks for the root's name.

1. **Final dump.** Runs `/opt/classora/bin/final-dump.sh` on the PROD host over SSM Run Command and waits (up to 60 minutes): `classora.dump` and `counts.json` to `s3://classora-archive-<suffix>/prod/<UTC timestamp>/`, a restore of that object into a scratch container with every table's row count compared, then `verified.json` ([`restore.md`](restore.md#final-dump-at-shutdown)). A failed or unverified dump stops the script; PROD stays.
2. **Check.** `infrastructure/scripts/prod-destroy-guard.sh` reads the newest `prod/*/verified.json` and requires: newer than PROD's instance launch, data volume and last `classora-deploy-prod` command; `mismatched` 0; the dump it names exists.
3. **Destroy PROD.** `make tf-destroy ENV=prod` runs the same guard, shows the destroy plan (40 resources, all `module.app_host`) and asks for `prod`. Then it offers to delete PROD's leftover snapshots and the unmanaged `/classora/prod/owner_initial_password`.
4. **Destroy monitoring.** `make tf-destroy ENV=monitoring` (type `monitoring`): Grafana, all telemetry, the `grafana` record, the SMTP user and `/classora/shared/otlp_endpoint`.
5. **DEV.**
   - `dev-only`: writes `monitoring_mode = "off"` (or `colocated`) to the git-ignored `infrastructure/terraform/envs/dev/monitoring.auto.tfvars`, so later plans keep it, then plans and applies `envs/dev`. Redeploy DEV afterwards so its agent stops: `gh workflow run deploy-dev.yml --ref develop`.
   - `all`: `make tf-destroy ENV=dev` (type `dev`) and its leftover snapshots.
6. **`all` only: destroy `shared`** (type `shared`): the VPC, the subnets and the ECR repositories with every image.

The script ends with every remaining `Project=classora` resource. The same steps can be run one by one with `make tf-destroy ENV=<root>`; `make tf-destroy ENV=prod` refuses without a fresh verified final dump, with "prod-destroy-guard: REFUSED: …".

**Verification.**
- `aws s3 ls s3://classora-archive-<suffix>/prod/ --recursive` lists `classora.dump`, `counts.json` and `verified.json` under one timestamp.
- `aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=classora --query 'ResourceTagMappingList[].ResourceARN'` shows no `Environment=prod` (and, for `all`, no `dev`) resource.
- `dig +short app.classora.dev` returns nothing.

**Expected downtime.** PROD is gone for good from step 3. In `dev-only`, DEV restarts its containers on the redeploy only. The final dump itself doesn't stop PROD (about a minute for a small database).

## Afterwards

- `release-prod` refuses with "PROD does not exist; apply envs/prod first". `deploy-dev` stays green with "DEV is not running (destroyed)" in `all` mode, or "ECR is not there" once `shared` is gone ([`deploy.md`](deploy.md)).
- GitHub keeps the `production` and `dev` environments and their variables; update `OPS_BUCKET` after any re-create.
- The budget alarm and the SES identity stay with `bootstrap`.

## Restore the archive into a fresh PROD

1. `make tf-plan ENV=shared` / `make tf-apply ENV=shared` if `shared` was destroyed, then `envs/dev` and a DEV deploy of the release you want (PROD only accepts commits DEV ran).
2. `make tf-plan ENV=prod`, `make tf-apply ENV=prod`; set `OPS_BUCKET` in the `production` environment; re-apply `envs/monitoring` if it exists.
3. Release that commit to PROD ([`release.md`](release.md)). Its schema must be the same or newer than the dump's.
4. After 30 days the archive objects are in Glacier Flexible Retrieval: request them back first and wait (3–5 hours):
   `aws s3api restore-object --bucket classora-archive-<suffix> --key prod/<timestamp>/classora.dump --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Standard"}}'` (the same for `counts.json`), then poll `aws s3api head-object … --query Restore` until `ongoing-request="false"`.
5. On the new PROD host, drill it: `/opt/classora/bin/restore.sh s3://classora-archive-<suffix>/prod/<timestamp>/classora.dump` (every table must match). The host may read only `prod/` of the archive.
6. Restore it for real with [`restore.md`](restore.md#real-restore-into-the-live-database), downloading the archive object instead of a nightly dump in step 3. Then bootstrap nothing: the Owner comes with the data.
