"""Payroll (architecture §4.1; ADR-0017; ``_docs/data-model.md`` §11).

M7-02 (#105) starts the module with tutor compensation: the effective-dated
pay terms (``tutor_pay_terms``) and rates per class kind
(``compensation_rates``), and the lookups the payroll calculation calls
(:func:`app.modules.payroll.service.rate_in_effect`,
:func:`app.modules.payroll.service.terms_in_effect`).

M7-03 (#106) adds the settings singleton, the period calendar and the
``payroll_periods`` chain the daily ``payroll:open_periods`` job opens.

M7-04c (#441) adds the effective-dated session pay rules
(``session_pay_rules``).

Other modules use :mod:`app.modules.payroll.service` and
:mod:`app.modules.payroll.schemas` only (ADR-0002).
"""
