# ADR-0011: Infrastructure as code, images and release

## Status

Accepted

## Date

2026-11-11

## Sign-off

Owner: JaimeSolisS. Direction and decisions 2–5, 10 and 11 given, and the M9 plan approved, on 2026-11-11 (M9 re-plan). Escalation E1 of the plan (deploy trigger): the owner chose option (a), a custom SSM document that accepts only a commit SHA. Recorded by M9-01 (#609).

## Context

Resolves architecture §10 **ISSUE-I5**, **ISSUE-I6** and **ISSUE-I10** (superseded) in [`../architecture.md`](../architecture.md) §10.10, and spec [`../spec.md`](../spec.md) W8.2–W8.4.

- Spec §10 planned Terraform with PR plans, DEV applies on merge, destructive-plan approval, SSM Run Command deployments and cloud-init + SSM provisioning.
- On 2026-11-11 the owner chose **Terraform for clean apply and destroy, with no PR plan pipeline**, and **a simple deploy script with a health check and rollback, not the SSM pipeline**.
- Decisions:
  - 3: ECR in us-east-2, pulled with the instance role, managed and destroyed by Terraform.
  - 4: DEV deploys automatically on every merge to `develop`, and the job must not turn `develop` red when DEV is destroyed.
  - 5: PROD deploys on the merge of the `develop` → `main` release PR, after owner approval in a protected `production` environment, and promotes DEV's tested digest.
  - 10: the AWS CLI profile is `jsolisdev`.
  - 11: the deploy is triggered through a custom SSM Run Command document that accepts only a commit SHA; there is no SSH.
- Environments live about 20 days. The AWS account also holds unrelated projects.

## Decision

### Terraform

- **Layout** in `infrastructure/terraform/`, one state per root, in an S3 backend with native lockfile locking (Terraform ≥ 1.10; version and AWS provider pinned):

| Root | Holds | Lifetime |
|---|---|---|
| `bootstrap` | state bucket, Route 53 zone (imported), GitHub OIDC provider and roles, SES identity, budget, PROD archive bucket | long-lived; not part of teardown |
| `shared` | VPC and subnets, ECR repositories | destroyed at full teardown |
| `envs/dev`, `envs/prod` | one `app_host` module each: EC2, EIP, data volume, security group, instance role, Parameter Store values, DNS record, ops bucket, snapshot policy, deploy document | ~20 days |
| `envs/monitoring` | the monitoring host (ADR-0014) | ~20 days |

- `bootstrap` starts with local state, then migrates it into the bucket it created.
- The backend configuration and personal values (alert e-mail) are git-ignored files or `TF_VAR_` variables. The repository is public, so they are never committed.
- **Applies run locally**, from the owner's machine with `AWS_PROFILE=jsolisdev`, through `make tf-plan` / `make tf-apply` / `make tf-destroy ENV=<root>`. The plan is shown and must be confirmed. An apply that creates billable resources needs the owner's go-ahead (given on 2026-11-11 for the resources in the M9 plan; anything outside it is asked again). There is **no PR plan pipeline and no apply from CI**. CI only runs `terraform fmt -check` and `terraform validate` (no AWS credentials).
- **Destroy is a normal operation.** `prevent_destroy` is set only on the state bucket, the hosted zone and the archive bucket. `make tf-destroy` requires typing the root's name. For `envs/prod` it refuses unless a verified final dump exists in the archive (ADR-0013).
- Every resource carries default tags `Project=classora`, `Environment=<root>`, `ManagedBy=terraform`. IAM policies are scoped by those tags or by `classora-*` names. Terraform never imports or references resources outside Classora.

### Images

- ECR repositories `classora/api` (API and worker), `classora/web` (edge, ADR-0010) and `classora/pgbouncer`. **Tag immutability** is on, and basic scan on push.
- Images are built **only on `develop`** (and checked, without pushing, on pull requests that change them), for arm64, and tagged `sha-<40-hex commit>`. Before push: `make scan-image` (ADR-0016 thresholds) and `make check-pdf-image` inside the arm64 API image.
- Extra tags record promotion: `dev-<sha>` after a successful DEV deploy, `prod-<sha>` after PROD. Adding a new tag to an existing digest is allowed under immutability.
- **Lifecycle policy** (rules in priority order, so a higher rule's match protects an image from lower rules): keep the newest 10 `prod-*`, 10 `dev-*` and 30 `sha-*` images; expire untagged images after 1 day. Repositories use `force_delete` so `shared` can be destroyed.

### Deploy

- **The deploy is one script on the host**, `/opt/classora/bin/deploy.sh <sha>`. Its steps:
  1. Download the release bundle for `<sha>` from the environment's ops bucket. The bundle holds the Compose files and the three image digests, and is written by the workflow.
  2. Render the env file from Parameter Store (ADR-0012).
  3. Save the current release as `previous`.
  4. Log in to ECR with the instance role and pull by digest.
  5. Make sure the data stack is up.
  6. Run `alembic upgrade head`, then the idempotent reference seeds, in a one-off container of the new API image on the **direct** connection.
  7. `docker compose up -d` the app stack.
  8. Poll `/health/ready` and the edge's `/` from inside the host for up to 90 s.
  9. On failure, bring the previous digests back up, check again, and exit non-zero as "rolled back". **Migrations are never reversed**, so they stay expand-and-contract.

  A migration failure stops before step 7, so the old version keeps serving.
- **Trigger (plan E1 (a)):** GitHub Actions assumes the environment's OIDC role and calls `ssm send-command` with a **custom SSM document `classora-deploy`**. Its only parameter is a SHA matching `^[0-9a-f]{40}$`, and it runs only `deploy.sh`. The role may send only that document, only to instances tagged with its environment. The workflow waits for the command result, and the job fails on non-zero. SSM is used only as this trigger and for Session Manager.
- **DEV** (`deploy-dev.yml`, GitHub environment `dev`, no reviewer): runs on every push to `develop`. Its steps: build, check, push, bundle, deploy, then tag `dev-<sha>`. If `/classora/dev/instance_id` is absent from Parameter Store, the deploy part is skipped with a `::notice::` and the run stays green. If ECR is absent (shared destroyed), build and push are skipped too.
- **PROD** (`release-prod.yml`, GitHub environment `production`, required reviewer: the owner, deployment branch `main`): runs on push to `main`.
  - It resolves the merged release pull request's **head commit on `develop`** and deploys that commit's image digest.
  - It refuses when that commit has no `sha-<sha>` image or no `dev-<sha>` tag (never deployed to DEV).
  - On success it adds `prod-<sha>` and the Git tag `vYYYY.MM.DD-<sha7>`.
  - A manual `workflow_dispatch` with a SHA (same checks, same approval) redeploys or rolls back.
- Neither workflow is part of `ci-required`. A failed deploy is a red run on `develop`/`main` and a GitHub notification, not a merge blocker.

### Delivery

M9-02 (#610) adds the arm64 image checks to CI; M9-05 (#613) creates the Terraform foundation (`bootstrap`, `shared`); M9-06 (#614) the DEV host; M9-07 (#615) the DEV publish and deploy workflow; M9-10 (#618) PROD, the release workflow and teardown.

## Amendments

### A1. A read-only role for the pre-approval check (2026-11-12, #618)

`classora-gha-prod` trusts only jobs in the `production` environment, and every such job waits for the owner's approval. The refusals above must happen **before** an approval is requested, so `release-prod.yml`'s `verify` job uses a third role, `classora-gha-release-check` (`bootstrap`). It trusts only this repository's jobs on `main` without an environment (immutable subject `…:ref:refs/heads/main`) and may only describe `classora/*` images, read `/classora/prod/instance_id` and describe instances. It can't push, tag, deploy or read a secret. Because the deploy and the `prod-<sha>` tag both need `classora-gha-prod`, they run in the one approved `deploy` job; the Git tag is a separate job with `contents: write` and no AWS access.

## Consequences

- ISSUE-I6 is solved without fast-forward-only `main`: PROD deploys the release PR's head commit, so the release-merge method doesn't matter.
- There is no automatic drift detection or apply. Changes to Terraform reach AWS only when someone runs `make tf-apply`. QA verifies live resources after the approved apply.
- A host whose SSM agent is broken can't be deployed. Recovery is an instance replacement (ADR-0010 data volume).
- `.github/workflows/` gains two workflows. `ci.yml` gains `images` and `infra` jobs that join `ci-required` through `changes.sh`.
- The spec §10 "IaC", "CI/CD" and "EC2 management" bullets, W8.2–W8.4 and architecture §9.2 were rewritten in M9-01 (#609).

## Alternatives considered

- **PR plan pipeline with destructive-change gate** (ISSUE-I10). Rejected by the owner: CI credentials, plan comments and approval routing for short-lived environments.
- **Apply from CI on merge.** Puts long-lived admin-level credentials (or a broad role) in CI, and destroy cycles become awkward.
- **SSM Run Command with `AWS-RunShellScript`.** The role could then run any command on the host.
- **Pull-based deploy (timer polling a "desired SHA").** No inbound call, but the workflow can't report the result (plan E1 (b)).
- **SSH from Actions.** Open port 22 and a stored key (plan E1 (c)).
- **Build on `main` for PROD.** Produces a different digest from the one DEV tested.
