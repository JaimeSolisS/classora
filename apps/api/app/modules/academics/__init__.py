"""Academics (architecture §4.1): session summaries (session closeouts,
M5-10 #77). Later issues add internal notes (#79), evaluations (#80) and
progress reports (#83).

The module owns ``session_closeouts`` and imports neither ``scheduling``
nor ``families`` nor ``courses`` (#77 E2): ``app.workflows.session_closeout``
composes it with ``scheduling.service`` and ``administration.service``.
Other code uses :mod:`app.modules.academics.service` and
:mod:`app.modules.academics.schemas` only (ADR-0002).
"""
