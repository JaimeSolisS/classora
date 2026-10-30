"""The PDF smoke check (``python -m app.cli pdf-smoke``, #98a D5).

Renders ``documents/smoke.html`` in every document language and prints the
byte size and page count of each. Every warning is an error while it runs, so
a missing ``libharfbuzz-subset`` (WeasyPrint's ``DeprecationWarning``) or any
render problem fails the check: ``make check-pdf-image`` runs it in the API
image, and the unit tests render the same document natively.
"""

import warnings
from datetime import date
from typing import Final, TextIO

from app.core import i18n, pdf
from app.core.money import Money

SMOKE_TEMPLATE: Final = "documents/smoke.html"

SMOKE_CENTER_NAME: Final = "Centro de Tutorías Aurora"
SMOKE_PERSON_NAME: Final = "Peña Ñandú"


def smoke_context() -> dict[str, object]:
    """Sample data for the smoke document (synthetic; no database)."""
    return {
        "center": {
            "name": SMOKE_CENTER_NAME,
            "address": "Av. Reforma 123, Col. Centro, Ciudad de México",
            "phone": "+52 55 0000 0000",
            "email": "contacto@example.com",
        },
        "name": SMOKE_PERSON_NAME,
        "issued_on": date(2026, 10, 28),
        "amount": Money("1250.00"),
    }


def render_smoke(lang: i18n.Language) -> pdf.RenderedPdf:
    """The smoke document in ``lang``."""
    return pdf.render_pdf_document(
        SMOKE_TEMPLATE,
        smoke_context(),
        lang=lang,
        title=i18n.t(lang, "common.smoke.title"),
    )


def run(out: TextIO, err: TextIO) -> int:
    """Render in es and en with warnings as errors; 0 on success, 1 on any
    exception (the message goes to ``err``)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for lang in i18n.LANGUAGES:
                rendered = render_smoke(lang)
                if rendered.refused_urls:
                    raise pdf.FetchRefusedError(
                        f"the smoke document refers to {list(rendered.refused_urls)}"
                    )
                print(
                    f"pdf-smoke {lang}: {len(rendered.data)} bytes, "
                    f"{rendered.page_count} page(s)",
                    file=out,
                )
    except Exception as exc:  # the check reports every failure
        print(f"pdf-smoke failed: {type(exc).__name__}: {exc}", file=err)
        return 1
    return 0
