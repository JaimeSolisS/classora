"""Billing (architecture §4.1; ADR-0006; ``_docs/data-model.md`` §3).

M6-04 (#89) starts the module with the service price catalog: catalog items
(``service_prices``), their effective-dated versions (``price_versions``)
and the price lookup the later billing issues call
(:func:`app.modules.billing.service.lookup_price`).

Other modules use :mod:`app.modules.billing.service` and
:mod:`app.modules.billing.schemas` only (ADR-0002).
"""
