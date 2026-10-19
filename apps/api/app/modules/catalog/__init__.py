"""Catalog: the academic catalog (spec §4, §12; architecture §4.1).

Education levels and the subjects taught at each level (#32). Later catalog
issues (periods and closures, #38; classrooms, #39) add their tables here with
the same patterns: ``is_active`` instead of deletion, ``PATCH`` with
``version``, a ``status`` filter that defaults to ``active`` and ``409
name_taken``.

Other modules use only :mod:`app.modules.catalog.service` and
:mod:`app.modules.catalog.schemas` (ADR-0002).
"""
