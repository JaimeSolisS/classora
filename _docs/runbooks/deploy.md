# Runbook: DEV deploys

How a merge reaches DEV, how to read a failed deploy, and how to redeploy or roll back by hand. The decisions behind it are in [ADR-0011](../adr/0011-infrastructure-as-code-images-and-release.md) and [ADR-0012](../adr/0012-secrets-and-configuration-delivery.md). The host itself (layout, units, Session Manager) is in [`host.md`](host.md). PROD releases promote DEV's tested images: [`release.md`](release.md).

Every AWS command below uses the owner's profile: `export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`.

## The automatic flow

Every push to `develop` (every merge, docs-only included) runs `.github/workflows/deploy-dev.yml` in the GitHub environment `dev`. It is not part of `ci-required`, so a red deploy never blocks a merge; it is a red run on `develop` and a GitHub notification.

| Job | What it does |
|---|---|
| `probe` | Assumes `classora-gha-dev` (OIDC). Checks that ECR (`classora/api`, `web`, `pgbouncer`) and `/classora/dev/instance_id` exist and that the instance is running. Checks whether `sha-<commit>` already exists in all three repositories. |
| `build` | Only when ECR exists. Builds the three arm64 images and runs the M9-02 checks (`check-images`, `check-pdf-image`, `smoke-edge`, `scan-image`). Pushes `sha-<commit>`; an image that already exists isn't pushed again (tags are immutable), and when all three exist the build is skipped. While DEV exists it uploads the release bundle `releases/<commit>.tgz` (Compose files, host scripts and units, the collector agent config, `images.env` with the three digests) to the DEV ops bucket. |
| `dev-data` | Only when DEV is running, in parallel with `build`. On a throwaway PostgreSQL (the host's pin) with `APP_ENV=dev`: migrations, reference seeds, the English simulation (`--seed 1`) and the `visitor` Owner; then `pg_dump -Fc --no-owner --no-acl` and exact row counts, uploaded as `releases/<commit>.dev-data.dump` and `releases/<commit>.dev-data.counts.json`. The host's nightly reset restores them ([`dev-data.md`](dev-data.md)). A commit from before #674 builds nothing (notice). |
| `deploy` | Only when DEV is running, after `build` and `dev-data`; a failed `dev-data` skips it. Sends the SSM document `classora-deploy-dev` with one parameter, the commit SHA, to the instance in `/classora/dev/instance_id`. The document runs `/opt/classora/bin/deploy.sh <sha>` as root (900 s limit). The job waits up to 15 minutes, prints the script's output and fails on a non-zero exit code. |
| `tag` | After a successful deploy, adds `dev-<commit>` to the three images. An existing `dev-<commit>` (a rerun) is fine. |

Deploys never overlap (`concurrency: deploy-dev`, not cancelled in progress). If several merges arrive while one deploys, GitHub keeps only the newest waiting run, so DEV ends on the newest commit.

When parts of the environment are destroyed, the run stays green with a `::notice::`:
- DEV destroyed (no `/classora/dev/instance_id`) or stopped: "DEV is not running (destroyed); deploy skipped". Build and push still run, so the images are ready when DEV returns. The bundle isn't uploaded, because the ops bucket belongs to DEV.
- ECR destroyed (`shared`): everything is skipped.

A failure to assume the role is red: it means `bootstrap` or the environment variables are broken.

### What `deploy.sh` does

1. Downloads the bundle and installs its host scripts and units.
2. Renders the env file from Parameter Store.
3. Remembers the current release as `previous`.
4. Pulls the images by digest.
5. Makes sure `db` is up.
6. In a one-off container of the **new** API image, as the database owner: `alembic upgrade head`, `sync-runtime-role`, the reference seeds; then `db-roles.sh`.
7. Switches `current` and starts every service.
8. Readiness gate, up to 90 s: `/health/ready` inside `api`, `python -m app.worker --healthcheck` inside `worker`, and the site's `/` through Caddy on the host (200 with a `Content-Security-Policy` header).
9. If not ready: switches back to `previous`, starts it and checks it again.

After a healthy start it starts the systemd units (so they are active and the next reboot stops and starts the stack in order), keeps the three newest releases plus `current` and `previous`, and removes ECR images that belong to neither.

Deploys take about 2 minutes on the host when nothing is pulled. The build takes most of a run.

## Reading a failed run

Open the run, then the `deploy` job. The step summary shows the SSM status, the exit code and the script's JSON line (`{"result": …, "sha": …, "previous": …, "duration_s": …}`). The step log has the full output in the "deploy.sh output" and "deploy.sh errors" groups.

| Exit | Result | Meaning | What to do |
|---|---|---|---|
| 0 | `deployed` | Live. | Nothing. |
| 10 | `migration-failed` | The migration step failed (Alembic, `sync-runtime-role`, the seeds or `db-roles.sh`). Nothing was switched; the old version still serves. Each migration is its own transaction, so the database is at whatever revision Alembic last committed. | Fix forward with a new commit. |
| 20 | `rolled-back` | The new release failed readiness. The previous release is back and healthy. Migrations stay applied. | Read the `ps` output and `compose.sh logs` on the host; fix forward. |
| 21 | `rollback-failed` or `failed-no-previous` | The new release failed readiness and the previous one didn't come back (or there was none). **DEV is down.** | Go to the host: `compose.sh ps`, `compose.sh logs --tail 200 api worker edge`, `journalctl -u classora-data -u classora-app`. Then roll back by hand (below) to a release you know is good. |
| — | job `deploy` skipped | `build` or `dev-data` failed. | Open the failed job. A `dev-data` failure is usually the simulation or the visitor step: reproduce it locally with `make seed-data-english` on a fresh database, then `python -m tests.factories.simulation.visitor`. |
| 30 | `not-changed` | Bundle download, env rendering, the deploy lock, the image pull or the database start failed. Nothing changed. | Usually transient (S3, ECR, SSM): rerun the run. A missing bundle means the `build` job didn't upload it. |

An SSM status other than `Success` with no exit code (`TimedOut`, `Cancelled`, `Undeliverable`) means the agent never ran or finished the script. Check `aws ssm describe-instance-information` (the agent must be `Online`) and the host.

The output never contains secrets: `deploy.sh` never prints the env file and never runs with `set -x`, and Compose gets the env file with `--env-file`.

## Redeploy or roll back by hand

The workflow takes an optional `sha` input, which must be a commit on the dispatched branch (`develop`):

- Redeploy the head: `gh workflow run deploy-dev.yml --ref develop`.
- Roll back to an earlier release: `gh workflow run deploy-dev.yml --ref develop -f sha=<40-hex commit>`. Its images are already in ECR, so the build is skipped; the bundle is uploaded again from that commit, and the deploy then runs as usual. Migrations aren't reversed, so the older code runs against the newer schema (see below).

To find the previous release on the host: `readlink /srv/classora-data/releases/previous`. On ECR, `dev-<sha>` tags mark every release that passed a DEV deploy.

Over Session Manager you can also run `sudo /opt/classora/bin/deploy.sh <sha>` for any release whose bundle is in the ops bucket (bundles expire after 30 days). The document can be sent by hand too: `aws ssm send-command --document-name classora-deploy-dev --instance-ids <id> --parameters sha=<sha>`.

## Migrations: expand, then contract

Deploys never reverse a migration, and a rollback runs the previous code against the new schema. So every migration must be compatible with the release before it:

- **Expand** in one release: add a nullable column, a new table or an index; write to both old and new shapes if needed.
- **Contract** in a later release, once no deployed code uses the old shape: drop the column or table, tighten the constraint.

Example: renaming `students.nickname` to `students.display_name`.
1. Release 1 adds `display_name` (nullable), backfills it and writes both columns. A rollback to release 0 still finds `nickname`.
2. Release 2 reads only `display_name`.
3. Release 3 drops `nickname`. Release 2, the one a rollback would return to, no longer uses it.

Never drop or rename a column that the previous release reads in the same release.

## GitHub environment `dev`

- Deployment branches: `develop` only. Its jobs are the only ones that can assume `classora-gha-dev` (the role trusts the OIDC subject `…:environment:dev`).
- No reviewers, no secrets. Variables:
  - `AWS_ROLE_ARN`: the `classora-gha-dev` role (bootstrap output `gha_role_arns.dev`);
  - `OPS_BUCKET`: the DEV ops bucket (envs/dev output `ops_bucket`). **A re-created DEV gets a new bucket suffix**: update it with `gh variable set OPS_BUCKET --env dev --body <new name>` before the next deploy.
- After re-creating DEV, run `gh workflow run deploy-dev.yml --ref develop`: the images usually exist already, and the run uploads the bundle and deploys.
