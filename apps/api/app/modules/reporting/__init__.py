"""Reporting (ADR-0002 D3; architecture §4, ISSUE-A2, ISSUE-F6).

The read-only reporting module. #117 (M7-14) creates it for the financial
reports (:mod:`~app.modules.reporting.finance`, the pure
:mod:`~app.modules.reporting.recognition`, the routes in
:mod:`~app.modules.reporting.router`), ahead of M8; there are no saved report
setups yet. It may read other modules' **models** for joins; derived values
come from their ``service`` seams. #124 (M8-07) adds the asynchronous exports
(:mod:`~app.modules.reporting.exports`, the task in
:mod:`~app.modules.reporting.tasks`) and the module's only tables,
``export_jobs`` and ``export_files``; it still never writes another module's
tables.
"""
