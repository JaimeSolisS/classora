"""Academics (architecture §4.1): session summaries (session closeouts,
M5-10 #77), session notes, internal and family-visible (M5-12 #79), and
evaluation templates, evaluations and the unified academic history (M5-13
#80). Later issues add progress reports (#83).

The module owns ``session_closeouts``, ``session_notes`` and the
evaluation tables, and imports neither ``scheduling`` nor ``families`` nor
``courses`` nor ``catalog`` (#77 E2): ``app.workflows.session_closeout``,
``app.workflows.session_notes`` and ``app.workflows.evaluations`` compose
it with ``scheduling.service``, ``families.service``, ``catalog.service``,
``courses.service`` and ``administration.service``.
Other code uses :mod:`app.modules.academics.service` and
:mod:`app.modules.academics.schemas` only (ADR-0002).
"""
