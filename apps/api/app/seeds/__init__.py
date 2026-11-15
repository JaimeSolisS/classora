"""Reference seeds (spec §10 Seed/test data).

Reference data is fixed data that every environment needs (for example roles
or education levels). It is applied with ``make seed`` (``python -m
app.seeds``), idempotently, in every ``APP_ENV``, including ``prod``. The
local example data (``make seed-data-spanish`` / ``make seed-data-english``,
``tests/factories/simulation/``) and the E2E and test fixture data
(``tests/factories/demo.py``) are separate and never run in ``prod``.

- :mod:`app.seeds.registry`: the ordered ``REFERENCE_SEEDS`` tuple.
- :mod:`app.seeds.upsert`: the upsert-by-natural-key helper every seed uses.
- :mod:`app.seeds.runner`: the command (head check, one Unit of Work, summary).
"""
