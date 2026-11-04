"""Finance (architecture §4.1; ADR-0017 E12, E15; ``_docs/data-model.md`` §12).

M7-11 (#114) starts the module with the expense configuration: categories
(``expense_categories``, five of them seeded with a ``system_key``) and vendors
(``vendors``), their routes (:mod:`~app.modules.finance.router`) and the seam
the expense issues call (:mod:`~app.modules.finance.service`). Expenses arrive
with #115, templates with #116.

Other modules use :mod:`app.modules.finance.service` and
:mod:`app.modules.finance.schemas` only (ADR-0002).
"""
