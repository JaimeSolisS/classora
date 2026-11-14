# Runbook: Terraform

How the AWS infrastructure is planned, applied and destroyed ([ADR-0011](../adr/0011-infrastructure-as-code-images-and-release.md)). Applies run from the owner's machine only; CI runs `make check-infra` (format and validate, no AWS credentials).

## Prerequisites

- Terraform 1.13.x (`terraform version`); the AWS provider is pinned in each root's `versions.tf` and `.terraform.lock.hcl`.
- AWS CLI profile `jsolisdev`. Every target uses `AWS_PROFILE=jsolisdev` unless the shell sets another profile.
- Personal values are never committed (the repository is public). Export them in the shell, or put them in a git-ignored `infrastructure/terraform/bootstrap/terraform.auto.tfvars`:
  - `TF_VAR_hosted_zone_id`: the Route 53 zone the registrar created for `classora.dev` (`aws route53 list-hosted-zones`).
  - `TF_VAR_alert_email`: the owner's address for SES alerts and budget e-mails.
- Git-ignored files: `*.tfstate*`, `.terraform/`, `*.tfvars`, `backend.hcl`, `tfplan`, `tfplan-destroy` and `*_override.tf`. `.terraform.lock.hcl` is committed.

## Roots

| Root | `ENV=` | Holds | Cost | Lifetime |
|---|---|---|---|---|
| `bootstrap` | `bootstrap` | state bucket, the adopted `classora.dev` zone, SES domain identity (DKIM, DMARC) and recipient, the `classora-monthly` budget, the archive bucket, GitHub deploy roles `classora-gha-dev` / `classora-gha-prod` | ≈ $0.50/month (the zone) plus S3 pennies | long-lived, never destroyed by `make` |
| `shared` | `shared` | VPC `10.40.0.0/16` with `dev`, `prod` and `monitoring` public subnets in one AZ, internet gateway (no NAT); ECR `classora/api`, `classora/web`, `classora/pgbouncer` | free without images; ECR storage ≈ $0.10/GB-month | until full teardown |
| `envs/dev`, `envs/prod`, `envs/monitoring` | `dev`, `prod`, `monitoring` | hosts (M9-06, M9-09, M9-10) | see those runbooks | ~20 days |

Each root has its own state, `<root>/terraform.tfstate` in the state bucket (`envs/<name>/terraform.tfstate` for environments), locked with S3 native lock files. Every resource carries `Project=classora`, `Environment=<root>`, `ManagedBy=terraform` and the cost tag `App` (#669): `classora-dev` for `envs/dev`, `classora-prod` for `envs/prod`, and `classora-shared` for `bootstrap`, `shared` and `envs/monitoring`. The tags come from each root's `provider "aws"` `default_tags`; EC2 root volumes repeat them in `root_block_device.tags`. Data Lifecycle Manager snapshots copy the data volume's tags, and `snapshot-drill.sh` tags its drill volume.

The deploy roles trust only this repository's **immutable** OIDC subjects: the repository has `use_immutable_subject: true` (`gh api repos/JaimeSolisS/classora/actions/oidc/customization/sub`), so a job in GitHub environment `dev` presents `sub = repo:JaimeSolisS@26722249/classora@1422814793:environment:dev` (`production` for PROD). The owner and repository IDs are the `github_owner_id` / `github_repository_id` variables (`gh api repos/JaimeSolisS/classora --jq '[.owner.id, .id]'`); if the repository is renamed or the subject setting changes, update them and re-apply `bootstrap`. Any job that names the environment can assume its role, so the `dev` environment's deployment branches are restricted to `develop` (M9-07, [`deploy.md`](deploy.md#github-environment-dev)), and the `production` environment is restricted to `main` with the owner as required reviewer (M9-10, [`release.md`](release.md#github-environment-production-and-variables)). `release-prod`'s pre-approval `verify` job uses a third, read-only role, `classora-gha-release-check`, which trusts `…:ref:refs/heads/main` (jobs on `main` without an environment).

The account-wide GitHub Actions OIDC provider (`token.actions.githubusercontent.com`) already exists in the account and belongs to another project. `bootstrap` only reads it. If it is ever removed, the deploy roles stop working until a provider for that URL exists again.

## Plan and apply

```bash
make tf-plan ENV=<root>    # init and save <root>/tfplan; review the whole plan
make tf-apply ENV=<root>   # shows the saved plan summary, asks "Type the root name to apply:"
```

- Read every line of the plan. Stop if it destroys or replaces anything you didn't expect.
- An apply that creates billable resources needs the owner's go-ahead.
- A wrong answer to the prompt applies nothing and keeps the plan. Terraform refuses a plan that has gone stale; run `tf-plan` again.

## First bootstrap and state migration

Done once per account (done on 2026-11-11, #613).

1. `make tf-plan ENV=bootstrap`. Without `infrastructure/terraform/backend.hcl` the root runs on local state (the script writes a git-ignored `local_backend_override.tf`). The plan must show **1 to import** (the zone, with only tags changing) and the new resources; nothing to destroy.
2. `make tf-apply ENV=bootstrap`.
3. `infrastructure/scripts/terraform.sh migrate-bootstrap`. It writes the git-ignored `backend.hcl` from the `state_bucket` output, moves the bootstrap state into `s3://<state bucket>/bootstrap/terraform.tfstate` and deletes the local state files.
4. `make tf-plan ENV=bootstrap` must report `No changes`.
5. `make tf-plan ENV=shared`, then `make tf-apply ENV=shared`.

On another machine, recreate `backend.hcl` by hand:

```hcl
bucket       = "<state_bucket output>"
region       = "us-east-2"
profile      = "jsolisdev"
encrypt      = true
use_lockfile = true
```

## After the first bootstrap (owner)

- **SES recipient:** click the "Amazon Web Services – Email Address Verification Request" e-mail sent to the alert address. Check with `aws sesv2 get-email-identity --email-identity <address> --region us-east-2` (`VerifiedForSendingStatus: true`).
- **SES domain:** DKIM verification takes from minutes up to 72 hours: `aws sesv2 get-email-identity --email-identity classora.dev --region us-east-2` shows `VerificationStatus: SUCCESS` and `DkimAttributes.Status: SUCCESS`.
- **Budget e-mails:** AWS Budgets registers the alert address as an AWS User Notifications e-mail contact and sends it an activation code. The contact stays `inactive`, and **no budget e-mail arrives**, until the owner enters that code (the e-mail's link, or Console → AWS User Notifications → Email contacts). Check with `aws notificationscontacts list-email-contacts --region us-east-1` (`status: active`).
- **Cost allocation tag:** the budget counts only `Project=classora` costs, which requires the `Project` user-defined cost allocation tag to be active. It appears in Billing up to 24 hours after the first tagged resource. Activate it in Billing → Cost allocation tags, or:

  ```bash
  aws ce update-cost-allocation-tags-status --region us-east-1 \
    --cost-allocation-tags-status TagKey=Project,Status=Active
  aws ce list-cost-allocation-tags --tag-keys Project --region us-east-1
  ```

  Activation can take up to 24 hours to apply, and costs before it are not counted.
- **`App` cost allocation tag (#669):** splits costs per environment (`classora-dev`, `classora-prod`, `classora-shared`). Activate it the same way after the first apply that adds it; the key appears in Billing up to 24 hours later:

  ```bash
  aws ce update-cost-allocation-tags-status --region us-east-1 \
    --cost-allocation-tags-status TagKey=App,Status=Active
  aws ce list-cost-allocation-tags --tag-keys App --region us-east-1
  ```

## Destroy

```bash
make tf-destroy ENV=<root>   # plans the destroy, shows it, asks "Type the root name to destroy:"
```

- `bootstrap` is refused: the state bucket, the zone and the archive bucket carry `prevent_destroy`, and the zone holds the registrar's NS records.
- `prod` is refused unless the archive holds a verified final dump newer than PROD's launch, data volume and last deploy (`infrastructure/scripts/prod-destroy-guard.sh`, ADR-0013). The whole end-of-window sequence is `make env-teardown` ([`teardown.md`](teardown.md)).
- `shared` removes the VPC and the ECR repositories **including their images** (`force_delete`). Destroy the `envs/*` roots first.
- To preview without the prompt: `terraform -chdir=infrastructure/terraform/<root> plan -destroy`.

Removing a bootstrap resource that isn't protected (for example the budget or the deploy roles) is a code change: delete it from the root, then plan and apply. Removing the state bucket, the zone or the archive needs the `prevent_destroy` line removed first, by an owner decision.

## Static checks

`make check-infra` runs `terraform fmt -check -recursive` and `terraform validate` for every root (`init -backend=false`) in the pinned `hashicorp/terraform` image, and `shellcheck` on `infrastructure/scripts/` and `infrastructure/host/` in the pinned `koalaman/shellcheck` image. It needs Docker only. Fix formatting with `terraform fmt -recursive infrastructure/terraform`.

It also runs `infrastructure/scripts/check-default-tags.sh` (bash and awk, no Docker): every root must be in its `App` map, every `provider "aws"` block (aliases included) must have `default_tags` with that root's `App` value, and every `root_block_device` `tags` block under `modules/` must have an `App` key. It prints `OK <root> App=<value>` per root and `FAIL <root>: <reason>` per problem. A new root needs an entry in the script's map. Run it alone with `infrastructure/scripts/check-default-tags.sh [terraform_dir]`.

## Tag check

`infrastructure/scripts/check-tags.sh` checks the `App` tag on the live account (#669). It is read-only (`describe*`, `list*` and `get*` calls only) and needs the AWS CLI v2 and bash, with the owner's profile:

```bash
AWS_PROFILE=jsolisdev infrastructure/scripts/check-tags.sh
```

It finds Classora's resources by the `Project=classora` tag, a `classora` name or ARN prefix, or membership in the Classora VPC: us-east-2 for regional resources, us-east-1 for global ones (IAM, Route 53, the budget). Other projects in the account are ignored. The expected value follows the `Environment` tag: `dev` → `classora-dev`, `prod` → `classora-prod`, `bootstrap`/`shared`/`monitoring` → `classora-shared`. It prints one line per finding, then a summary:

- `MISSING <arn-or-id> <name>`: no `App` tag.
- `WRONG <arn-or-id> App=<got> expected=<want>`.
- `PENDING <snapshot-id> pre-apply DLM snapshot, expires <date>`: a Data Lifecycle Manager snapshot taken before the apply that added `App`. Old snapshots are not re-tagged. The policy keeps 7, so each one is deleted within 8 days of its `StartTime`. After that date, an untagged snapshot counts as `MISSING`.
- `EXCEPTION <id> <reason>`: the VPC's default security group (and its rules), default network ACL and main route table. AWS creates them with the VPC, they cost nothing, and Terraform doesn't manage them.
- `checked=N ok=N missing=N wrong=N pending=N exceptions=N`.

It exits 0 when `missing=0` and `wrong=0`, 1 otherwise, and 2 on a usage or credential error. E-mail identities are printed as `***@<domain>`. ECR images and S3 objects are not checked: they can't carry the tag, and their cost is billed to the tagged repository or bucket.

