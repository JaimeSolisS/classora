# Classora delivery process

- GitHub Issues are the only active backlog. `_docs/tasks.md` is the archived import source.
- Work on one issue at a time.
- PM grooms the issue before engineering starts. Never skip grooming. Role: [`_docs/team/pm.md`](team/pm.md).
- Engineer implements the groomed issue, writes tests, and commits changes locally before QA. The engineer does not close the issue. Role: [`_docs/team/software-engineer.md`](team/software-engineer.md).
- QA verifies each acceptance criterion independently, does not modify code, and posts `QA: PASS` or `QA: FAIL`. Role: [`_docs/team/qa-engineer.md`](team/qa-engineer.md).
- On FAIL, the issue goes back to the engineer with the QA comment, and QA checks it again.
- The orchestrator (the main session) launches PM, Engineer and QA as subagents. It does not groom, implement or test itself.
- README gate: after every issue, the engineer reviews root `README.md` and reports `README updated: ...` or `README reviewed: no update necessary`. QA verifies the verdict before PASS.
- The orchestrator pushes the engineer's commits to `main` and closes the issue only after QA PASS and a README verdict.
- Do not silently add requirements; create a follow-up issue for new scope.
- Release ([spec §10, CI/CD](spec.md#10-system-architecture-decisions)): use pull requests and CI checks before merging. DEV receives approved `develop` merges automatically. PROD requires GitHub Environment manual approval.
