# ADR-0012: Secrets and configuration delivery

## Status

Accepted

## Date

2026-11-11

## Sign-off

Owner: JaimeSolisS. Direction given, and the M9 plan approved, on 2026-11-11 (M9 re-plan; decisions 6, 7, 10 and 11). No open escalation. Recorded by M9-01 (#609).

## Context

Resolves architecture §10 **ISSUE-I1** in [`../architecture.md`](../architecture.md) §10.10, and the secret part of spec [`../spec.md`](../spec.md) §14 and §18 item 8.

- Spec §10 planned "GitHub Secrets delivered via SSM to EC2". ISSUE-I1 proposed that GitHub Actions write Parameter Store `SecureString` values, and that Run Command never carry them.
- With Terraform applied locally (ADR-0011) and environments recreated every ~20 days, the secrets are generated per environment. No human ever needs to know the app's database password.
- The repository is public. `app.core.config` reads only the process environment, and its error messages never contain values. PgBouncer writes its auth file at container start (`infrastructure/docker/pgbouncer/entrypoint.sh`).
- Decision 6: Grafana's admin password lives in Parameter Store. Decision 7: alerts go out by SES e-mail. Decision 11: the deploy document accepts only a commit SHA.

## Decision

- **Source of truth:** AWS Systems Manager **Parameter Store**, standard tier, under `/classora/<env>/…`.
  - `SecureString` (AWS-managed `aws/ssm` key) for secrets;
  - `String` for plain settings (`app_origin`, `instance_id`, `otlp_endpoint`).
- **Generation:** Terraform `random_password` creates every secret per environment:
  - the PostgreSQL owner password;
  - the runtime role password once #165 has landed;
  - the Grafana admin password;
  - the SES SMTP credentials (an IAM user allowed only `ses:SendRawEmail` from `alerts@classora.dev`, whose SMTP password Terraform derives).

  Recreating an environment creates new secrets.
- **GitHub holds no application secret.** Workflows authenticate with OIDC. Role ARNs and the region are repository *variables*.
- **Delivery to the host:**
  - The instance role may `ssm:GetParameter(s)ByPath` only on its own `/classora/<env>/` path and `/classora/shared/`, and `kms:Decrypt` only through SSM.
  - `render-env.sh` runs at boot and at every deploy. It writes `/opt/classora/env/app.env`, mode `0600`, owner root, which Compose passes as `env_file`. The file is rewritten atomically and never printed.
  - The SSM deploy document receives only a commit SHA (ADR-0011). No secret appears in workflow logs, SSM command output, `docker inspect` of images, or cloud-init user data.
- **Images contain no secrets.** A check in the image job inspects the image config and layers' environment for known secret variable names.
- **Terraform state contains the generated secrets.** This is accepted, and the state bucket is the control: private, public access blocked, SSE-S3, versioned, TLS-only bucket policy, `prevent_destroy`, readable only by the `jsolisdev` principal.
- **Rotation:** recreating the environment rotates everything. Between recreations, `terraform apply -replace=random_password.<name>` followed by a deploy rotates one secret. The runbook covers the PostgreSQL password, which also needs `ALTER ROLE` before the deploy. No automatic rotation.
- **Personal values** (the owner's alert e-mail, the backend bucket name) are git-ignored `*.auto.tfvars` / `backend.hcl` or `TF_VAR_` variables, never committed.

### Delivery

M9-05 (#613) creates the paths and IAM; M9-06 (#614) renders the env file on the DEV host; M9-09 (#617) adds the Grafana and SES SMTP secrets.

## Consequences

- No secret is created, typed or copied by a person, except the owner's own Grafana login, read with `aws ssm get-parameter --with-decryption`.
- Anyone with read access to the state bucket can read the secrets. Only the owner's IAM user has it, and the GitHub roles don't.
- The SES SMTP user is a long-lived access key. It is limited to sending from one address, and recreated with the monitoring environment.
- Spec §10 stack table "Configuration" now reads ".env locally; Terraform-generated Parameter Store values rendered to a root-only env file on EC2", and the spec §14 "Secrets handling" bullet was reworded, in M9-01 (#609).

## Alternatives considered

- **GitHub Secrets written to Parameter Store by CI** (ISSUE-I1 as proposed). Adds a second copy of each secret and a CI role that can write them, for no benefit when Terraform runs locally.
- **AWS Secrets Manager.** $0.40 per secret per month, and rotation features this project doesn't use.
- **Passing secrets as Run Command parameters.** They would be visible in command history.
- **SNS e-mail instead of SES SMTP for alerts.** No access key, but it contradicts decision 7, and the messages come from a generic AWS sender.
