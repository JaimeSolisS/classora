"""Academics (architecture §4.1): session summaries (session closeouts,
M5-10 #77) and session notes, internal and family-visible (M5-12 #79).
Later issues add evaluations (#80) and progress reports (#83).

The module owns ``session_closeouts`` and ``session_notes`` and imports
neither ``scheduling`` nor ``families`` nor ``courses`` (#77 E2):
``app.workflows.session_closeout`` and ``app.workflows.session_notes``
compose it with ``scheduling.service``, ``families.service`` and
``administration.service``.
Other code uses :mod:`app.modules.academics.service` and
:mod:`app.modules.academics.schemas` only (ADR-0002).
"""
