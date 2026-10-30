"""PDF rendering (#98a D1-D5, E3, E4; ADR-0015 E14, ADR-0006 E15).

The PDF tests render with WeasyPrint, so they need Pango on the host (README
prerequisites; CI installs it). They assert on the text pypdf extracts, never
on bytes or images: fonts and timestamps differ between macOS, CI and Docker.
"""

import io
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, FileSystemLoader, UndefinedError
from pypdf import PdfReader

from app.core import pdf, pdf_smoke

API_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TEMPLATES = Path(__file__).resolve().parent / "pdf_fixtures"

SMOKE_TEXT = {
    "es": ["octubre", "28 de octubre de 2026", "Página 1 de 1", "Importe"],
    "en": ["October", "October 28, 2026", "Page 1 of 1", "Amount"],
}


def pdf_text(data: bytes) -> tuple[int, str, str | None]:
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() for page in reader.pages)
    title = reader.metadata.title if reader.metadata else None
    return len(reader.pages), text, title


@pytest.fixture
def no_network(monkeypatch):
    """Fail the test on any socket connection or urllib open."""

    def refuse(*args, **kwargs):
        raise AssertionError("the PDF renderer opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)


@pytest.fixture
def fixture_templates(monkeypatch):
    """Let the environment also load the test-only templates."""
    loader = ChoiceLoader(
        [FileSystemLoader(FIXTURE_TEMPLATES), FileSystemLoader(pdf.TEMPLATES_DIR)]
    )
    monkeypatch.setattr(pdf.ENVIRONMENT, "loader", loader)


# --- Jinja2 (no WeasyPrint) ---------------------------------------------------


def test_the_environment_autoescapes_and_is_strict():
    assert pdf.ENVIRONMENT.autoescape is True
    assert pdf.ENVIRONMENT.undefined.__name__ == "StrictUndefined"
    assert pdf.ENVIRONMENT.extensions == {}


@pytest.mark.parametrize("lang", ["es", "en"])
def test_render_html_sets_the_language_and_escapes_values(lang):
    context = {**pdf_smoke.smoke_context(), "name": "<b>x</b>"}
    html = pdf.render_html(pdf_smoke.SMOKE_TEMPLATE, context, lang=lang)

    assert f'<html lang="{lang}">' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_a_missing_variable_raises():
    context = pdf_smoke.smoke_context()
    del context["amount"]
    with pytest.raises(UndefinedError):
        pdf.render_html(pdf_smoke.SMOKE_TEMPLATE, context, lang="es")


def test_the_center_contact_lines_are_optional():
    context = {**pdf_smoke.smoke_context(), "center": {"name": "Solo Nombre"}}
    html = pdf.render_html(pdf_smoke.SMOKE_TEMPLATE, context, lang="en")
    assert "Solo Nombre" in html
    assert "Phone:" not in html
    assert "Email:" not in html


@pytest.mark.parametrize("name", sorted(pdf.BOUND_NAMES))
def test_the_context_cannot_override_the_bound_helpers(name):
    context = {**pdf_smoke.smoke_context(), name: "x"}
    with pytest.raises(ValueError, match="may not set"):
        pdf.render_html(pdf_smoke.SMOKE_TEMPLATE, context, lang="es")


def test_render_html_refuses_an_unknown_language():
    with pytest.raises(ValueError, match="unknown language"):
        pdf.render_html(
            pdf_smoke.SMOKE_TEMPLATE,
            pdf_smoke.smoke_context(),
            lang="fr",  # type: ignore[arg-type]
        )


def test_the_page_footer_uses_css_counters():
    assert pdf.page_footer_css("es") == (
        '"Página " counter(page) " de " counter(pages)'
    )
    assert pdf.page_footer_css("en") == '"Page " counter(page) " of " counter(pages)'


def test_css_strings_escape_quotes_and_markup():
    value = pdf._css_string('a"b\\c</style>&')
    inner = value[1:-1]
    assert value.startswith('"') and value.endswith('"')
    assert not any(char in inner for char in '"<>&')
    assert inner.count("\\") == 5  # one escape each for " \ < > &


# --- WeasyPrint (needs Pango) -------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_smoke_document_renders_in_each_language(lang, no_network):
    rendered = pdf_smoke.render_smoke(lang)

    assert rendered.data.startswith(b"%PDF-")
    assert rendered.page_count == 1
    assert rendered.refused_urls == ()
    pages, text, title = pdf_text(rendered.data)
    assert pages == 1
    assert pdf_smoke.SMOKE_CENTER_NAME in text
    assert pdf_smoke.SMOKE_PERSON_NAME in text
    assert "$1,250.00" in text
    for expected in SMOKE_TEXT[lang]:
        assert expected in text
    assert title == ("Documento de prueba" if lang == "es" else "Test document")


def test_render_pdf_returns_the_bytes(no_network):
    data = pdf.render_pdf(
        pdf_smoke.SMOKE_TEMPLATE, pdf_smoke.smoke_context(), lang="en", title="T"
    )
    assert data.startswith(b"%PDF-")


def test_markup_in_a_value_appears_literally_in_the_pdf(no_network):
    context = {**pdf_smoke.smoke_context(), "name": "<b>x</b>"}
    data = pdf.render_pdf(pdf_smoke.SMOKE_TEMPLATE, context, lang="es", title="T")
    _, text, _ = pdf_text(data)
    assert "<b>x</b>" in text


def test_every_fetch_is_refused(no_network, fixture_templates):
    rendered = pdf.render_pdf_document(
        "documents/fetches.html", {**pdf_smoke.smoke_context()}, lang="en", title="F"
    )

    assert rendered.data.startswith(b"%PDF-")
    refused = set(rendered.refused_urls)
    assert "http://127.0.0.1:9/x.png" in refused
    assert "http://127.0.0.1:9/import.css" in refused
    assert "file:///etc/passwd" in refused
    assert (pdf.TEMPLATES_DIR / "relative.png").as_uri() in refused
    _, text, _ = pdf_text(rendered.data)
    assert "root:" not in text


def test_the_smoke_command_prints_size_and_pages(capsys):
    from app import cli

    assert cli.main(["pdf-smoke"]) == 0
    # Other tests may have configured JSON logging to stdout: keep our lines.
    out = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("pdf-smoke")
    ]
    assert [line.split(":")[0] for line in out] == ["pdf-smoke es", "pdf-smoke en"]
    assert all(line.endswith("bytes, 1 page(s)") for line in out)


def test_the_smoke_command_fails_on_a_warning(monkeypatch, capsys):
    import warnings

    def warn(lang):
        warnings.warn(
            "HarfBuzz-Subset will be required", DeprecationWarning, stacklevel=2
        )

    monkeypatch.setattr(pdf_smoke, "render_smoke", warn)
    assert pdf_smoke.run(sys.stdout, sys.stderr) == 1
    assert "DeprecationWarning" in capsys.readouterr().err


# --- Lazy import (A2) ---------------------------------------------------------


def test_weasyprint_is_not_imported_by_the_app():
    """Starting the API, the CLI and importing the PDF module load no Pango."""
    code = (
        "import sys, app.main, app.cli, app.core.pdf, app.core.pdf_smoke; "
        "app.main.create_app(); "
        "assert 'weasyprint' not in sys.modules, 'weasyprint imported'"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, the test's interpreter
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "APP_ENV": "local",
            "APP_ORIGIN": "http://127.0.0.1:5173",
            "DATABASE_URL": "postgresql://u:p@127.0.0.1:6432/classora",
            "DATABASE_DIRECT_URL": "postgresql://u:p@127.0.0.1:5432/classora",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
