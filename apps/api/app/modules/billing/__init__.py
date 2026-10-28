"""Billing (architecture §4.1; ADR-0006; ``_docs/data-model.md`` §3).

M6-04 (#89) starts the module with the service price catalog: catalog items
(``service_prices``), their effective-dated versions (``price_versions``)
and the price lookup the later billing issues call
(:func:`app.modules.billing.service.lookup_price`). M6-05 (#90) adds the
family billing account and the ledger core: the append-only ledger tables,
the posting primitives (``ledger.py``) and the derived balances, statuses
and history (``balances.py``); see :mod:`app.modules.billing.service`.

Other modules use :mod:`app.modules.billing.service` and
:mod:`app.modules.billing.schemas` only (ADR-0002).
"""
