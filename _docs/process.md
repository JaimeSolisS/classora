# Classora delivery process

- GitHub Issues are the only active backlog. `_docs/tasks.md` is the archived import source.
- Work on one issue at a time.
- PM grooms the issue before engineering starts. Never skip grooming. Role: [`_docs/team/pm.md`](team/pm.md).
- Engineer implements the groomed issue, writes tests, and commits changes locally before QA. The engineer does not close the issue. Role: [`_docs/team/software-engineer.md`](team/software-engineer.md).
- QA verifies each acceptance criterion independently, does not modify code, and posts `QA: PASS` or `QA: FAIL`. Role: [`_docs/team/qa-engineer.md`](team/qa-engineer.md).
- On FAIL, the issue goes back to the engineer with the QA comment, and QA checks it again.
- The orchestrator (the main session) launches PM, Engineer and QA as subagents. It does not groom, implement or test itself.
- README gate: after every issue, the engineer reviews root `README.md` and reports `README updated: ...` or `README reviewed: no update necessary`. QA verifies the verdict before PASS.
- Branches and pull requests: the engineer commits locally on a branch named `m<milestone>-<nn>-<slug>` (for example `m1-11-security-scanning`). The orchestrator pushes it, opens a pull request to `develop` and merges it only when the `ci-required` check is green, QA has posted PASS and the README verdict is recorded; the orchestrator then closes the issue. `develop` reaches `main` through a release pull request with the same required check, which the human opens and merges.
- CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs on every pull request to `develop` or `main`. Each check is one `make` target, so a red check is reproduced locally with the same command.
- GitHub repository settings are configured by the repository owner, or by the orchestrator only when the owner explicitly authorizes a specific change: the `develop` branch (created from `main`) as the default branch, and one ruleset on `develop` and `main` that allows changes only through pull requests, requires the `ci-required` status check with the branch up to date before merging, and blocks force pushes and deletions.
- Do not silently add requirements; create a follow-up issue for new scope.
- Release ([spec §10, CI/CD](spec.md#10-system-architecture-decisions)): use pull requests and CI checks before merging. DEV receives approved `develop` merges automatically and PROD (`main`) requires GitHub Environment manual approval; both deployments arrive with M9, so until then merging to `develop` or `main` deploys nothing.
