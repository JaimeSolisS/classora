# Runbook: PROD releases

How a release reaches PROD, how to approve and verify it, how to roll back, and what a refusal means. The decisions are in [ADR-0011](../adr/0011-infrastructure-as-code-images-and-release.md) (decision 5: PROD deploys on the merge of the `develop` → `main` release pull request, after the owner's approval, and promotes the digests DEV tested). The host is the same as DEV's ([`host.md`](host.md)), and so is `deploy.sh` ([`deploy.md`](deploy.md)).

Every AWS command below uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`.

## Preconditions

- PROD exists: `make tf-plan ENV=prod` shows `No changes` (create it with `make tf-plan ENV=prod` and `make tf-apply ENV=prod`; about US$18/month). After a (re-)create, re-apply `envs/monitoring` so its 4318 rule admits PROD's security group, and update the `OPS_BUCKET` variable (below).
- The `develop` head you want to release was deployed to DEV: its `deploy-dev` run is green, so `dev-<sha>` exists on the three images. A merge that DEV never deployed (for example while DEV was destroyed) is refused.
- The GitHub environment `production` and the variables are set (last section).

## Release

1. Open the release pull request `develop` → `main` (owner only, `_docs/process.md`). Wait for green CI and merge it with any method (merge commit or squash).
2. The push to `main` starts `.github/workflows/release-prod.yml`:

   | Job | What it does |
   |---|---|
   | `resolve` | No AWS. Finds the merged pull request for the pushed commit (base `main`, head `develop`) and takes its **head SHA**: the `develop` commit that DEV ran. A push to `main` that isn't a release merge is refused. |
   | `verify` | Assumes the read-only `classora-gha-release-check` role (trusts only jobs on `main` without an environment). Requires `sha-<sha>` and `dev-<sha>` on the **same digest** in `classora/api`, `web` and `pgbouncer`, the commit on `develop`, and a running PROD (`/classora/prod/instance_id`). |
   | `deploy` | Environment `production`: **waits for the owner's approval**. Then assumes `classora-gha-prod`, uploads `releases/<sha>.tgz` built from the `develop` commit's tree (not `main`'s) plus the verified digests to the PROD ops bucket, sends `classora-deploy-prod`, waits for `deploy.sh` (same readiness gate, rollback and exit codes as DEV) and adds `prod-<sha>` to the three images. |
   | `tag` | The annotated Git tag `vYYYY.MM.DD-<sha7>` on the `develop` commit. The only job with `contents: write`. |

3. **Approve.** The run shows "Waiting for review" on `deploy`. In GitHub: the run → **Review deployments** → `production` → **Approve and deploy**. From the CLI:

   ```bash
   run=$(gh run list --workflow release-prod.yml --limit 1 --json databaseId --jq '.[0].databaseId')
   env_id=$(gh api repos/JaimeSolisS/classora/actions/runs/$run/pending_deployments --jq '.[0].environment.id')
   gh api -X POST repos/JaimeSolisS/classora/actions/runs/$run/pending_deployments \
     -F "environment_ids[]=$env_id" -f state=approved -f comment="release approved"
   gh run watch "$run"
   ```

   Rejecting (`state=rejected`) ends the run without touching PROD. An approval request waits up to 30 days.

Releases never overlap (`concurrency: release-prod`, never cancelled). PROD is not part of `ci-required`: a failed release is a red run on `main` and a notification.

## Verify

- `curl -sI https://app.classora.dev/` returns `HTTP/2 200` with the M9-02 security headers and HSTS; `curl -s https://app.classora.dev/health/ready` returns 200.
- The digests PROD runs are the ones DEV tested. For each of `api`, `web`, `pgbouncer`:
  - `aws ecr describe-images --repository-name classora/api --image-ids imageTag=prod-<sha> --query 'imageDetails[0].[imageDigest,imageTags]'` lists `sha-<sha>`, `dev-<sha>` and `prod-<sha>` on one digest;
  - on PROD (Session Manager, `sudo -i`): `cat /srv/classora-data/releases/current/images.env` and `docker inspect --format '{{.Config.Image}}' classora-api-1 classora-edge-1 classora-pgbouncer-1` show the same digests; the same commands on DEV show what DEV ran.
- `git fetch --tags && git tag --points-at <sha>` shows `vYYYY.MM.DD-<sha7>`.
- Grafana's dashboards show `env=prod` data after a few minutes, if the monitoring host exists ([`monitoring.md`](monitoring.md)).

**Expected downtime:** none for the SPA (the edge serves static files throughout). The API and worker containers are recreated in a few seconds after the migrations. A release that fails readiness is rolled back about 2 minutes after the switch (90 s gate, then the previous release comes back in about 15 s); see the DEV drills in [#615](https://github.com/JaimeSolisS/classora/issues/615#issuecomment-6813508937).

## Refusals

A refusal is a red run with the reason in the summary, and **no approval request**:

| Where | Message | Meaning |
|---|---|---|
| `resolve` | "… is not the merge of a develop -> main release pull request" | Something other than a release PR reached `main`. Nothing to release. |
| `resolve` | "sha must be a 40-hex commit" | A manual run with a malformed SHA. |
| `verify` | "… has no sha-<sha> image (never built by deploy-dev)" | The commit was never built (a random SHA, or one older than the ECR lifecycle keeps). |
| `verify` | "… has no dev-<sha> tag (never deployed to DEV, or DEV's deploy failed)" | DEV never ran this commit healthily. Deploy it to DEV first (`gh workflow run deploy-dev.yml --ref develop -f sha=<sha>`), then rerun the release run or dispatch it. |
| `verify` | "sha-<sha> and dev-<sha> are different digests" | Should never happen (tags are immutable); investigate ECR before releasing. |
| `verify` | "… is not a commit on develop" | Only `develop` commits are released. |
| `verify` | "PROD does not exist; apply envs/prod first" / "PROD is not running" | Create or start PROD, then rerun. |
| `deploy` | exit 10, 20, 21 or 30 | As on DEV ([`deploy.md`](deploy.md#reading-a-failed-run)). **21 means PROD is down**: roll back by hand at once. |

## Roll back or redeploy

Run the workflow by hand on `main` with the SHA of an earlier release. It goes through the same checks and the same approval:

```bash
aws ecr describe-images --repository-name classora/api --filter tagStatus=TAGGED \
  --query 'reverse(sort_by(imageDetails,&imagePushedAt))[].imageTags[?starts_with(@,`prod-`)]' --output text
gh workflow run release-prod.yml --ref main -f sha=<40-hex develop commit>
```

On the host, `readlink /srv/classora-data/releases/previous` is the release before the current one. Migrations are never reversed, so the older code runs against the newer schema (expand and contract, [`deploy.md`](deploy.md#migrations-expand-then-contract)). A `deploy.sh` rollback after a failed readiness gate is automatic; a manual rollback is this dispatch. Expected downtime: as a release.

## First release and the PROD Owner

The first PROD release is the owner's. PROD exists since 2026-11-12 with no release, so `https://app.classora.dev` refuses connections until it.

1. Merge the release PR `develop` → `main`, then approve the `deploy` job (above).
2. Verify (above). The first deploy creates the database, runs the migrations and the reference seeds (no demo data), and Caddy reuses the certificate obtained on 2026-11-12.
3. Bootstrap the Owner (username `jaimesolis`, display name "Jaime Solis"). The password is generated straight into Parameter Store and piped to `manage.sh`; it is never printed or typed:

   ```bash
   python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(24))' |
     aws ssm put-parameter --name /classora/prod/owner_initial_password --type SecureString --value file:///dev/stdin
   instance=$(aws ssm get-parameter --name /classora/prod/instance_id --query Parameter.Value --output text)
   cid=$(aws ssm send-command --document-name AWS-RunShellScript --instance-ids "$instance" \
     --comment "bootstrap the PROD Owner" --query Command.CommandId --output text \
     --parameters 'commands=["set -o pipefail; aws ssm get-parameter --region us-east-2 --name /classora/prod/owner_initial_password --with-decryption --query Parameter.Value --output text | /opt/classora/bin/manage.sh bootstrap-owner jaimesolis --display-name \"Jaime Solis\" --password-stdin"]')
   sleep 30
   aws ssm get-command-invocation --command-id "$cid" --instance-id "$instance" \
     --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
   ```

   The output says the Owner was created. Only the PROD host role may read `/classora/prod/*` among the hosts (the DEV and monitoring hosts are denied).
4. Sign in at `https://app.classora.dev` with the password (`aws ssm get-parameter --name /classora/prod/owner_initial_password --with-decryption --query Parameter.Value --output text`, on your own screen), change it, then delete the parameter: `aws ssm delete-parameter --name /classora/prod/owner_initial_password`.
5. Record the window's end date (about 20 days later) for [`teardown.md`](teardown.md).

## GitHub environment `production` and variables

- Environment `production`: required reviewer the owner (`JaimeSolisS`), self-review allowed (the owner merges and approves), deployment branches `main` only, administrators can't bypass. Its jobs are the only ones that can assume `classora-gha-prod` (trust: the OIDC subject `…:environment:production`). Check with `gh api repos/JaimeSolisS/classora/environments/production`.
- Environment variables (no secrets):
  - `AWS_ROLE_ARN`: `classora-gha-prod` (bootstrap output `gha_role_arns.prod`);
  - `OPS_BUCKET`: the PROD ops bucket (envs/prod output `ops_bucket`). **A re-created PROD gets a new suffix**: `gh variable set OPS_BUCKET --env production --body <new name>`.
- Repository variable `AWS_ROLE_RELEASE_CHECK_ARN`: `classora-gha-release-check` (bootstrap output `gha_release_check_role_arn`), read-only: ECR `DescribeImages`, `/classora/prod/instance_id`, `DescribeInstances`.
