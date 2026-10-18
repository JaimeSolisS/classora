# ADR-0016: CI security tooling

## Status

Accepted

## Date

2026-10-17

## Sign-off

Owner: JaimeSolisS. Decisions #11 E1–E5 approved as proposed on 2026-10-17 (GitHub issue #11, M1-11, orchestrator decision record).

## Context

Spec §10 (Quality and security) asks for dependency scanning, secret detection, static security analysis and image scans, and spec W8.1 says every pull request runs security scans. The spec names no tools. Issue #10 (M1-10) set the CI rules this ADR keeps: only four GitHub Actions (`actions/checkout`, `actions/setup-node`, `actions/cache`, `astral-sh/setup-uv`), each pinned by commit SHA; every check is one `make` target; `ci-required` is the one required check; workflow permissions are `contents: read`.

In March 2026 the `aquasecurity/trivy-action` tags were force-pushed to malicious commits and a backdoored Trivy v0.69.4 binary was published. Scanner actions with movable tags are therefore avoided, and scanner images are pinned by digest.

## Decision

### Tools (E1) and how they run (E2)

No new GitHub Action is added. Each scanner runs inside a `make` target, identically on a laptop (Docker, uv, Node 24) and in CI. Every scanner version is defined once, as a variable at the top of the `Makefile`; the workflow contains no scanner version. Images are pinned by their multi-architecture index digest (`name:tag@sha256:…`; the tag is informational, the digest is what Docker pulls).

| Scan | Tool | Pin (Makefile variable) | Make target |
|---|---|---|---|
| Python dependencies | pip-audit (PyPA), via `uvx` | `PIP_AUDIT_VERSION` = 2.10.1 | `make audit-api` |
| npm dependencies | `pnpm audit`, built into the pinned pnpm (ADR-0001) | pnpm 12.14.0 via Corepack | `make audit-web` |
| Secrets | gitleaks (Docker image) | `GITLEAKS_IMAGE` = v8.30.1 `@sha256:c00b6bd0…` | `make scan-secrets`, `make scan-secrets-diff` |
| SAST | Semgrep Community Edition (Docker image) | `SEMGREP_IMAGE` = 1.179.0 `@sha256:93963d92…` | `make sast` |
| SAST (Python) | Ruff `S` rules (flake8-bandit), in the existing lint | Ruff from `apps/api/uv.lock` | `make lint-api` |
| Container images | Trivy (Docker image) | `TRIVY_IMAGE` = 0.75.0 `@sha256:af6acf9a…` | `make scan-image IMAGE=<ref>` |

Grouping targets: `make audit-deps` (both dependency audits) and `make security` (`audit-deps`, `scan-secrets`, `sast`; not the image scan). Never pin Trivy v0.69.4.

What each scan covers:

- **pip-audit** audits `uv export --frozen --all-groups --no-emit-project` of `apps/api/uv.lock` (runtime and dev dependencies; the API and the worker share this lockfile and image) with `--require-hashes --disable-pip`, so nothing is installed or resolved. Advisory data: the PyPI vulnerability service (OSV).
- **pnpm audit** reads `pnpm-lock.yaml` for every workspace package (`apps/web`, `packages/api-client`), production and dev dependencies. Nothing is installed.
- **gitleaks** scans git history with its default rules plus `.gitleaks.toml`: `make scan-secrets` covers all commits on all refs; `make scan-secrets-diff BASE= HEAD=` covers only `BASE..HEAD`. Findings are always redacted (`--redact`).
- **Semgrep** runs registry packs `p/python`, `p/javascript`, `p/typescript` and `p/react`, plus Classora's own rules in `.semgrep/classora.yml`, over `apps/api/app`, `apps/api/migrations`, `apps/web/src` and `packages/`, with exclusions in `.semgrepignore` (dependencies, build output, the generated API client). It reads files on disk, not `.gitignore`, so new untracked files are scanned locally. The packs are fetched anonymously from the Semgrep registry; `--metrics=off`, no login, no token. The registry's TypeScript/React rules for `eval`/`new Function` are WARNING and it has no failing rule for `dangerouslySetInnerHTML` with a non-constant value, so the two Classora rules (`classora-ts-eval`, `classora-react-dangerously-set-inner-html`) report them as ERROR. In Python the same patterns fail through Ruff (`S307` eval, `S602` `shell=True`) and Semgrep's `subprocess-shell-true` (ERROR).
- **Ruff `S`** is part of `[tool.ruff.lint] select` in `apps/api/pyproject.toml`; tests ignore only `S101` (assert), `S105` and `S106` (fake test credentials).
- **Trivy** scans an image's OS and language packages for vulnerabilities only (`--scanners vuln`), through the read-only Docker socket or from a registry. Its database is cached in the Docker volume `classora-trivy-cache`.

No scanner needs credentials or sends telemetry.

### Where and when (E3)

`.github/workflows/ci.yml` has three jobs, `security-deps` (`make audit-deps`), `security-secrets` (`make scan-secrets`, plus `make scan-secrets-diff` with the PR base and head SHAs on `pull_request`) and `security-sast` (`make sast`). They are **not** path-filtered and run on every pull request, push and manual run, so a new advisory or a secret in any file (documentation included) turns the pull request red. `ci-required` needs them and treats a skipped security job as a failure, so it stays the one required check. There is no scheduled run: new advisories against unchanged lockfiles are caught by the next pull request and by Dependabot alerts.

### Thresholds (E4)

| Scan | Fails on | Printed only |
|---|---|---|
| Python dependencies | any known vulnerability (pip-audit reports no severity) | — |
| npm dependencies | high and critical advisories, including dev dependencies | moderate, low |
| Secrets | any finding, full history and PR commits | — |
| Semgrep | findings of severity ERROR | WARNING, INFO (a separate, non-failing pass) |
| Ruff `S` | every enabled rule | — |
| Images (M9-06) | CRITICAL with a fixed version (`--ignore-unfixed`) | HIGH and CRITICAL (first, non-failing pass) |

Dev dependencies count because dev tools (Vite, Storybook, pytest plugins) run in CI with repository access.

### Exceptions

Exceptions live only in committed, reviewable files. Each one names the reason and a review date, and is approved in the pull request that adds it (the owner, or QA in review). `infrastructure/scripts/check-scan-exceptions.sh` runs before each scan and fails the target when an entry has no reason, no review date, or a review date in the past; an expired exception is re-approved with a new date or removed.

| Scanner | File | Format |
|---|---|---|
| pip-audit | `apps/api/pip-audit-ignore.txt` | `ID  # reason, review-by YYYY-MM-DD`, passed as `--ignore-vuln` |
| pnpm audit | `pnpm-workspace.yaml`, `auditConfig.ignoreGhsas` | `- GHSA-…  # reason, review-by YYYY-MM-DD` |
| gitleaks | `.gitleaks.toml` | `[[allowlists]]` scoped to one rule and one path and the exact match (or to a commit, for history), with a comment; or an inline `gitleaks:allow` with a reason |
| Semgrep | source line | `# nosemgrep: <rule-id>` (`// nosemgrep: <rule-id>` in TypeScript) with a reason on the same or the previous line |
| Ruff `S` | source line | `# noqa: S<nnn>` with a reason on the same or the previous line |
| Trivy | `.trivyignore` | `# reason` line, then `CVE-…  exp:YYYY-MM-DD` (Trivy itself stops ignoring after `exp`) |

At acceptance the lists are empty except for one gitleaks allowlist entry: the fake DSN passwords in `apps/api/tests/unit/test_job_rules.py` (commit `b214bf2`, M1-16), which exist to prove that credentials are never logged.

A failing scan is fixed first (upgrade the dependency, remove the secret, change the code). If that is not possible, add a reasoned exception as above. A real secret that reached git history is rotated first; rewriting history is the owner's decision.

### Image scanning and M9-06

`make scan-image` is not run in pull-request CI, which builds no images. M9-06 attaches it to the production image build: same target, same thresholds, after `docker build` (with `--pull`, so base-image security fixes are included) and before the image is pushed. A failing scan stops the push.

### Dependabot (E5)

`.github/dependabot.yml` enables weekly version updates for the `github-actions` ecosystem only, grouped into one pull request against `develop`, so the SHA pins of the approved actions stay current. There are no `pip`/`uv`/`npm` version-update pull requests.

### Owner-only GitHub settings

- Turn on **Dependabot alerts** (Settings → Code security). Dependabot version updates need no switch beyond the committed file, but target `develop`, which must exist.
- If the repository is public, also turn on **secret scanning** and **push protection** (free for public repositories).

No runtime technology is added: no new app dependency, no Redis, Celery or RDS, no Dockerfile or Compose change.

## Consequences

- Each security job pulls its scanner image (Semgrep's is the largest), adding roughly 30–60 s; the jobs run in parallel with the #10 jobs.
- Because the scans are not path-filtered, a newly published advisory can turn an unrelated pull request red. This is intended: it forces triage through the exception process.
- Semgrep registry packs change over time, so a pull request can see a new rule without a repository change. Pinning the Semgrep image keeps the engine stable; a noisy new rule is handled through `nosemgrep` with a reason.
- Bumping a scanner means changing its Makefile variable (version and digest) in an ordinary pull request.
- `make security` needs Docker, uv and Node 24 on the developer machine, the same as the rest of the toolchain.

## Alternatives considered

- **OSV-Scanner for both lockfiles and images.** One tool and data source, but `uv.lock` and pnpm lockfile v9 support was unproven and it would replace Trivy for M9-06. Not chosen.
- **GitHub-native (CodeQL, secret scanning, Dependabot alerts only).** Strong SAST, but free only for public repositories, not reproducible locally, and needs a new action and `security-events: write`. Can be added later if the repository goes public.
- **Bandit as a separate tool.** Ruff's `S` rules implement the same checks inside the existing lint.
- **Vendor GitHub Actions for the scanners.** More third-party code with repository access, gitleaks-action needs a paid licence for organizations, and no one-command local reproduction. Not chosen (E2).
- **A separate `security.yml` with its own required check and a weekly schedule.** A second required check and failure emails for little gain over Dependabot alerts (E3).
- **Stricter (npm moderate, Semgrep WARNING) or looser (production dependencies only) thresholds.** More noise from build-time dev tools, or blind spots in tools that run in CI (E4).

## References

- [`../spec.md`](../spec.md) §10 Quality and security, §10 CI/CD, §15 W8, §16 Secrets handling
- [`../architecture.md`](../architecture.md) §9.2 Delivery pipeline, §11
- GitHub issues #10 (CI pipeline, action allowlist, `ci-required`) and #11 (this decision)
