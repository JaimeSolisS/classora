# Runbook: load test on DEV

How to measure Classora on the DEV host under the agreed load profile, and how to leave DEV as it was. The profile, the thresholds, the results and the sizing decision are in [ADR-0018](../adr/0018-sizing-load-and-recovery-objectives.md) and [`../load-test-report.md`](../load-test-report.md). Everything runs from the owner's machine with the AWS CLI (`export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`), Docker and `uv`.

## Safety rules

- **DEV only.** Every script refuses any target but `dev`, finds the host from `/classora/dev/instance_id` and checks its `Name` tag. `make load-test` refuses `app.classora.dev`. Never run any of this against PROD.
- **The real `classora` database is never written.** Load mode serves a scrubbed simulated center from a side database, `classora_load`. `dev-down.sh` compares `classora`'s exact row counts from before and after.
- **Time box.** Load mode stays on for under 24 hours. While it is on, the owner's own DEV login does not work (the account lives in `classora`, which is not being served).
- **No merge to `develop` during a run.** A merge redeploys DEV and silently ends load mode. Check `gh run list --workflow deploy-dev.yml` first. If it happens, run `dev-up.sh dev` again.
- **Login budget.** A run signs in at most 40 accounts once each, under `login.ip` (50 per 15 minutes per IP). Wait 15 minutes before restarting a run that crashed during setup.
- **Avoid 08:30–08:40 UTC** (the nightly backup) or record it.
- **No credential leaves the machine.** The scrubbed passwords are only in `infrastructure/load/accounts.local.json` (gitignored, mode 0600). Delete it when the work is done. Never paste a password, cookie or token into a file, issue or PR.

## Steps

### 1. Prepare the dataset (once)

```bash
make db-shared
infrastructure/load/prepare-dataset.sh --upload
```

It builds the simulated center (`make seed-data-spanish-native`, FULL profile: 40 students, 12 tutors, 4 staff, 30 sign-in guardians) in its own native database `classora_load_prep`, then scrubs it:

- every sign-in account gets a new random password, written only to the accounts file;
- sessions, account tokens, idempotency keys and rate-limit counters are emptied;
- the current privacy notice is accepted for every account;
- the full README's `classora-sim-*` passwords are checked against every stored hash and must all fail.

It dumps the database (`pg_dump -Fc --no-owner --no-acl`) and uploads it to `s3://classora-dev-ops-…/backups/load-<YYYYMMDD>/classora.dump`. The nightly `restore.sh latest` only matches `backups/<YYYY-MM-DD>/`, so it never picks this object, and the bucket's 14-day lifecycle removes it if `dev-down.sh` does not.

### 2. Put DEV in load mode

```bash
infrastructure/load/dev-up.sh dev                          # restore once, lift the per-IP limit
infrastructure/load/dev-up.sh dev --rate-limit default     # the staff-600 run: the default 600/min
infrastructure/load/dev-up.sh dev --tuning candidate.yaml  # stack a candidate Compose override
infrastructure/load/dev-up.sh dev --fresh                  # restore the dump again
```

Over Run Command it creates `classora_load`, restores the dump (it prints `RESTORE_SECONDS`), gives `classora_app` the grants migration 0096 gives in each database, writes `/root/classora-load-override.yaml` (api and worker on `classora_load`; `API_RATE_LIMIT_PER_MINUTE=60000` unless `--rate-limit default`) and recreates api and worker with it. It prints the database each container uses and the api's limit, then checks that the site answers. Record the start time it prints.

### 3. Run

```bash
infrastructure/load/run.sh p1            # 30 min, 1x (32 users)
infrastructure/load/run.sh p2            # 15 min, 3x (96 users)
infrastructure/load/run.sh p3            # ramp 3x to 8x over 10 min, stops at the breakpoint
infrastructure/load/run.sh staff-600 5m  # staff only at 3x, after dev-up.sh --rate-limit default
infrastructure/load/run.sh p1 10m        # the post-merge confirmation (AC11)
```

`run.sh` starts the host sampler (`docker stats`, memory, swap, load, I/O wait and PostgreSQL activity every 10–30 seconds), runs `make load-test`, collects the samples, reads the window's server-side histograms from Prometheus on the monitoring host (read-only, `deployment_environment_name="dev"` only) and prints a summary. Everything goes to `infrastructure/load/out/<profile>-<time>/` (gitignored).

`make load-test PROFILE=p1|p2|p3|staff-600 [TARGET=…] [VUS=…] [DURATION=…]` runs k6 alone. A dry run against a local stack: `make load-test PROFILE=p1 TARGET=http://host.docker.internal:<port> VUS=4 DURATION=1m K6_ARGS=--no-thresholds DEBUG=1`, with the local API's `APP_ORIGIN` set to that same URL. `DEBUG=1` prints every unexpected response.

The pass thresholds are judged server-side. k6's own thresholds include the round trip from your machine to us-east-2 (about 50 ms).

### 4. Leave DEV as it was

```bash
infrastructure/load/dev-down.sh dev
```

It compares `classora`'s exact row counts with the ones taken when load mode started (they must match), recreates the release's services without the override (api and worker back on `classora`, the default limit), drops `classora_load`, removes the override files, deletes every `backups/load-*` object, and checks that `https://dev.classora.dev/` answers 200 and `/api/v1/auth/me` answers 401. It fails if any check fails. It is safe to repeat.

Then check by hand that the owner can sign in on DEV, and delete the accounts file once nobody needs it.

## When something goes wrong

- **A run stops in setup with `login …: 429`:** the login budget is spent. Wait 15 minutes.
- **`dev-up.sh` times out waiting for readiness:** read `sudo /opt/classora/bin/compose.sh logs --tail 100 api worker` on the host, then run `dev-down.sh dev`.
- **A deploy happened during a run:** the run measured the redeploy. Discard it, run `dev-up.sh dev` and start again.
- **The P3 ramp ran DEV out of memory:** containers restart by themselves (`unless-stopped`). Record it as a finding, then run `dev-down.sh dev`.
