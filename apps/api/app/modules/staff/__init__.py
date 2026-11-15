"""Staff: tutor profiles (#43; spec §4; architecture §4.1).

A tutor is a user holding the ``tutor`` role. Their contact details are the
account's ``email`` and ``phone`` (``identity``); this module stores only the
professional experience (``tutor_profiles``). Teaching subjects (#44),
availability (#45) and documents (M5-08) follow.

Other modules use only :mod:`app.modules.staff.service` and
:mod:`app.modules.staff.schemas` (ADR-0002).
"""
