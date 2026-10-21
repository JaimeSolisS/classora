# Classora agent instructions

- Read relevant sections of `_docs/spec.md` and `_docs/architecture.md`.
- Follow `_docs/process.md`; GitHub Issues are the only active backlog.
- Each agent works on exactly one groomed issue; the orchestrator keeps at most
  two issues in flight, each in its own worktree (`_docs/process.md`).
- Do not implement extra features, adjust acceptance criteria during coding,
  or introduce Redis/Celery/RDS without a new approved ADR.
- Use tests, migrations and authorization checks suited to the change.
- Document findings and exact test commands/results in the issue.
- AFTER EVERY ISSUE OR FOLLOW-UP CODING PROMPT: review root `README.md`.
  Update only if public onboarding, implemented capabilities, tech stack,
  verified commands, deployment status or links changed. Never put task logs
  or internal notes in the README. Report one of:
  `README updated: ...` / `README reviewed: no update necessary`.
- QA must verify the README gate before PASS. The orchestrator must not close
  an issue without a QA PASS and README verdict.
