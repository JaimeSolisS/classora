"""Payroll (architecture §4.1; ADR-0017; ``_docs/data-model.md`` §11).

M7-02 (#105) starts the module with tutor compensation: the effective-dated
pay terms (``tutor_pay_terms``) and rates per class kind
(``compensation_rates``), and the lookups the payroll calculation calls
(:func:`app.modules.payroll.service.rate_in_effect`,
:func:`app.modules.payroll.service.terms_in_effect`).

Other modules use :mod:`app.modules.payroll.service` and
:mod:`app.modules.payroll.schemas` only (ADR-0002).
"""
