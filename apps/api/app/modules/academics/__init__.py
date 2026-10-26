"""Academics (architecture §4.1): session summaries (session closeouts,
M5-10 #77), session notes, internal and family-visible (M5-12 #79), and
evaluation templates, evaluations and the unified academic history (M5-13
#80), learning plans with objectives and milestones (M5-14 #81), homework
assignments with their submissions and grades (M5-15 #82), and progress
reports with reviewer approval (M5-16 #83).

The module owns ``session_closeouts``, ``session_notes``, the evaluation
tables, the learning plan tables, the homework tables and
``progress_reports``, and imports neither ``scheduling`` nor ``families``
nor ``courses`` nor ``catalog`` (#77 E2): ``app.workflows.session_closeout``,
``app.workflows.session_notes``, ``app.workflows.evaluations``,
``app.workflows.learning_plans``, ``app.workflows.assignments`` and
``app.workflows.progress_reports`` compose it with ``scheduling.service``,
``families.service``, ``catalog.service``, ``courses.service``,
``identity.service`` and ``administration.service``.
Other code uses :mod:`app.modules.academics.service` and
:mod:`app.modules.academics.schemas` only (ADR-0002).
"""
