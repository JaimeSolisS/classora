"""Billing (architecture §4.1; ADR-0006; ``_docs/data-model.md`` §3).

M6-04 (#89) starts the module with the service price catalog: catalog items
(``service_prices``), their effective-dated versions (``price_versions``)
and the price lookup the later billing issues call
(:func:`app.modules.billing.service.lookup_price`). M6-05 (#90) adds the
family billing account and the ledger core: the append-only ledger tables,
the posting primitives (``ledger.py``) and the derived balances, statuses
and history (``balances.py``); see :mod:`app.modules.billing.service`. M6-06
(#91) adds the billing settings; M6-10 (#95) the discounts, scholarships and
custom prices (``discounts.py``, ``discount_config.py``,
``custom_prices.py``) and the shared price calculation
(:func:`app.modules.billing.service.quote`). M6-11 (#96) records payments
and allocates them, and allocates account credit later (``payments.py``).

Other modules use :mod:`app.modules.billing.service` and
:mod:`app.modules.billing.schemas` only (ADR-0002).
"""
