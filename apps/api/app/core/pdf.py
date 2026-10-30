"""Server-side PDF rendering: Jinja2 HTML templates to PDF with WeasyPrint
(ADR-0015 E14, ADR-0006 E15, #98a).

The rules (#98a D1-D3):

- **One Jinja2 environment** (:data:`ENVIRONMENT`) over ``app/templates/``,
  ``autoescape`` on for every template, ``StrictUndefined`` (a missing
  variable is an ``UndefinedError``, never an empty string), no extensions.
  Each render binds ``t`` (:func:`app.core.i18n.t` for the render's
  language), ``money`` (:func:`app.core.i18n.format_money`), ``date``
  (:func:`app.core.i18n.format_date` for the language) and ``lang``; a caller
  context can't override them. Templates are code-owned. When M8-04 (#121)
  makes templates editable, those templates must be rendered with
  ``jinja2.sandbox.SandboxedEnvironment`` (ADR-0015), never this environment.
- **No fetches.** WeasyPrint gets a URL fetcher that refuses every URL
  (``http(s)``, ``file:``, ``data:``, anything), so a template can never make
  the server read a file or call the network (an SSRF/LFI guard for later
  staff-editable templates). Templates embed their CSS in ``<style>`` and
  images as inline SVG. A refused resource is skipped and the render goes on;
  :attr:`RenderedPdf.refused_urls` lists them.
- **Data comes from the caller.** The renderer reads nothing from the
  database: the center block and every value are in the context (for issued
  documents, the stored snapshot).
- **Synchronous and CPU-bound.** Route handlers call :func:`render_pdf`
  through ``anyio.to_thread.run_sync`` after their read transaction; the
  worker (M8-07) calls it directly.
- **WeasyPrint is imported lazily**, only inside the render, so importing
  this module (and starting the API, exporting OpenAPI, running migrations)
  needs no Pango on the host.

Templates extend ``documents/base.html`` (the branded layout, E15) and fill
its ``title`` and ``content`` blocks.

This module is shared infrastructure (ADR-0002): it imports no domain module.
"""

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined
from markupsafe import Markup

from app.core import i18n
from app.core.money import Money

TEMPLATES_DIR: Final = Path(__file__).resolve().parent.parent / "templates"
"""``app/templates/``: the root of every document template."""

ENVIRONMENT: Final = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    extensions=(),
)
"""The one environment for code-owned templates (see the module rules)."""

BOUND_NAMES: Final = frozenset({"t", "money", "date", "lang", "page_footer_css"})
"""Names each render binds; a caller context may not use them."""


class FetchRefusedError(ValueError):
    """Raised by the PDF URL fetcher for every URL (no fetches, D3)."""


@dataclass(frozen=True, slots=True)
class RenderedPdf:
    """A rendered PDF: its bytes, its page count and the refused URLs."""

    data: bytes
    page_count: int
    refused_urls: tuple[str, ...]


def _css_string(text: str) -> str:
    """``text`` as a CSS string literal that is also safe inside ``<style>``:
    anything but plain letters, digits, spaces and a few punctuation marks is a
    CSS hex escape (so no quote, backslash, ``<`` or ``&`` survives)."""
    escaped = "".join(
        char if char.isalnum() or char in " .,:;-_()/" else f"\\{ord(char):06x}"
        for char in text
    )
    return f'"{escaped}"'


def page_footer_css(lang: str) -> Markup:
    """The CSS ``content`` value of the "page X of Y" footer from
    ``common.document.page``, with ``{page}``/``{pages}`` as CSS counters."""
    text = i18n.message(lang, "common.document.page")
    parts: list[str] = []
    for index, piece in enumerate(i18n.PLACEHOLDER.split(text)):
        if index % 2:
            if piece not in ("page", "pages"):
                raise ValueError(f"unknown page footer placeholder {piece!r}")
            parts.append(f"counter({piece})")
        elif piece:
            parts.append(_css_string(piece))
    # Built from the code-owned catalog and CSS-escaped above.
    return Markup(" ".join(parts))  # noqa: S704


def _refuse_undefined(value: object) -> None:
    # StrictUndefined fails only when printed or tested, not when passed to a
    # function: the helpers refuse it the same way (UndefinedError).
    if isinstance(value, Undefined):
        value._fail_with_undefined_error()


def _money(value: object) -> str:
    _refuse_undefined(value)
    if not isinstance(value, Money):
        raise TypeError("money() takes a Money")
    return i18n.format_money(value)


def _date_in(lang: i18n.Language) -> Callable[[object], str]:
    def format_date(value: object) -> str:
        _refuse_undefined(value)
        if not isinstance(value, date):
            raise TypeError("date() takes a date")
        return i18n.format_date(lang, value)

    return format_date


def render_html(
    template: str, context: Mapping[str, object], *, lang: i18n.Language
) -> str:
    """Render ``template`` (a path under ``app/templates/``) in ``lang``."""
    language = i18n.resolve_language(lang)
    if language != lang:
        raise ValueError(f"unknown language {lang!r}")
    clash = BOUND_NAMES & context.keys()
    if clash:
        raise ValueError(f"the context may not set {sorted(clash)}")
    return ENVIRONMENT.get_template(template).render(
        {
            **context,
            "lang": language,
            "t": functools.partial(i18n.t, language),
            "money": _money,
            "date": _date_in(language),
            "page_footer_css": page_footer_css(language),
        }
    )


def _write_pdf(html: str, *, title: str) -> RenderedPdf:
    # Lazy import (#98a A2): WeasyPrint loads Pango through ctypes/cffi at
    # import time, so it is never imported at module import.
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    refused: list[str] = []

    class RefusingFetcher(URLFetcher):  # type: ignore[misc]
        def fetch(self, url: str, headers: object = None) -> object:
            refused.append(url)
            raise FetchRefusedError(f"PDF documents fetch nothing: {url}")

    document = HTML(
        string=html,
        base_url=str(TEMPLATES_DIR) + "/",
        url_fetcher=RefusingFetcher(),
    ).render()
    document.metadata.title = title
    data = document.write_pdf()
    if not isinstance(data, bytes):  # pragma: no cover - WeasyPrint contract
        raise TypeError("WeasyPrint returned no PDF bytes")
    return RenderedPdf(
        data=data, page_count=len(document.pages), refused_urls=tuple(refused)
    )


def render_pdf_document(
    template: str,
    context: Mapping[str, object],
    *,
    lang: i18n.Language,
    title: str,
) -> RenderedPdf:
    """Render ``template`` in ``lang`` to a PDF whose title metadata is
    ``title``; returns the bytes with the page count and refused URLs."""
    return _write_pdf(
        render_html(template, {**context, "document_title": title}, lang=lang),
        title=title,
    )


def render_pdf(
    template: str,
    context: Mapping[str, object],
    *,
    lang: i18n.Language,
    title: str,
) -> bytes:
    """Render ``template`` in ``lang`` to PDF bytes (see the module rules:
    synchronous, CPU-bound, no fetches)."""
    return render_pdf_document(template, context, lang=lang, title=title).data
