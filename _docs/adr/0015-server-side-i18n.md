# ADR-0015: Server-side i18n

## Status

Accepted

## Date

2026-10-27

## Sign-off

Owner: JaimeSolisS. Decision items E1–E13 and E15 (ADR-0006), or E14 (ADR-0015), taken as recommended (option (a) for each), owner-delegated, on 2026-10-27 (GitHub issue #86, M6-01). No amendments. The orchestrator's decision record explicitly approves the new runtime dependencies of E14 (Jinja2 and WeasyPrint, with the Debian Pango and font packages in the API image), installed by #98.

## Context

Resolves architecture §10 **ISSUE-X1** (server-rendered text language) in [`../architecture.md`](../architecture.md) §10.12. Records decision item **E14** of issue #86. The rest of #86 (E1–E13, E15) is [ADR-0006](0006-financial-ledger-idempotency-and-numbering.md).

- Spec [`../spec.md`](../spec.md) §1: the application is **bilingual** (Spanish and English), notifications are in-app only, and the center produces branded non-tax payment receipts.
- Spec §6: downloadable branded PDF receipts.
- Spec §10 (application stack): **react-i18next** for Spanish and English on the client; **WeasyPrint** PDFs and openpyxl XLSX for reports, with "background generation via worker".
- ISSUE-X1 proposed gettext catalogs plus Babel, "supporting libraries [that] need approval". None of gettext tooling, Babel, Jinja2 or WeasyPrint is in `apps/api/pyproject.toml` today.
- **File deferral** (owner decision 2026-10-21, architecture §7.3): no uploads or stored files in this version; generated PDFs are streamed on request and never stored.
- Already in practice and recorded here: Problem Details `code` values are stable keys (architecture §4.4); notifications are stored as `{type, params}` and rendered on the client (#41); the web default language is `es` (`DEFAULT_LANGUAGE` in `apps/web/src/i18n/language.ts`); the user's language is stored in `user_preferences.language`.

## Decision

### Language-neutral data (E14 (a))

- Problem Details `code` values are stable keys that the **client** translates.
- Notifications are stored as `{type, params}` and rendered on the client (#41), so a language switch applies to old notifications too.
- **Stored data stays language-neutral:** charges and other records store structured fields (kind, subject, minutes, period), not text. The only stored free text is staff-entered (a manual charge's description, reasons), shown as typed.

### PDFs

- HTML templates rendered with **Jinja2**, with autoescape on and `StrictUndefined`, then converted to PDF with **WeasyPrint**.
- Templates are code-owned. When M8-04 makes templates editable, it must render them with Jinja2's **sandboxed** environment.

### Server message catalogs

- **JSON files per language**, `apps/api/app/i18n/locales/{es,en}/<namespace>.json`, the same format as the web catalogs.
- A small `app/core/i18n.py` provides `t(lang, key, **params)`, with named `{param}` placeholders only.
- A **key-parity unit test** checks that `es` and `en` have the same keys.

### Formatting without Babel

- **Money** is formatted as `$1,250.00`, the same in es-MX and en, matching the web's `Intl.NumberFormat('es-MX')`.
- **Dates** use month names from the catalogs.

### Document language

- A document uses the recipient's stored `user_preferences.language`, or the center default `es` when there is none (the web `DEFAULT_LANGUAGE`).
- The language is stored on the document record (for receipts, `receipts.language`, ADR-0006 E13).

### New runtime dependencies (approved here, added by #98)

- `weasyprint` (BSD-3-Clause), current major, pinned `>=N,<N+1`.
- `jinja2` (BSD-3-Clause), 3.x. `markupsafe` is already in the lock.
- Debian packages `libpango-1.0-0`, `libpangoft2-1.0-0` and `fonts-dejavu-core` in `infrastructure/docker/api.Dockerfile`, which the worker image shares.
- `brew install pango` as a native-stack (macOS) prerequisite, with a README update in that issue.
- #98 (or its infrastructure split) confirms the licences and Python 3.14 support on PyPI and records the check date.
- **gettext and Babel are not added.**

### Rendering mode

- Receipts are rendered **synchronously** in the request and streamed, never stored (owner decision 2026-10-21).
- Spec §10's "background generation via worker" stays the rule for large reports and exports (M8-07).

### Implementation (#98a, 2026-10-30)

Recorded by #383 (M6-13a, the infrastructure split of #98). The decision above is unchanged.

- **Pins:** runtime `weasyprint>=70.0,<71` (locked 70.0, released 2026-09-08) and `jinja2>=3.1.6,<4` (locked 3.1.6); dev `pypdf>=6.20.1,<7` (locked 6.21.4), for the PDF text assertions in the tests. Licences and Python 3.14 support checked on PyPI by PM on 2026-10-28 and against the installed package metadata on 2026-10-30.
- **New locked packages and licences:**

  | Package | Version | Licence |
  |---|---|---|
  | weasyprint | 70.0 | BSD-3-Clause; Python ≥ 3.10, 3.14 classifier |
  | jinja2 | 3.1.6 | BSD-3-Clause; pure Python |
  | pydyf | 0.13.0 | BSD |
  | tinycss2 | 1.5.1 | BSD |
  | cssselect2 | 0.10.1 | BSD |
  | tinyhtml5 | 2.1.0 | MIT |
  | webencodings | 0.6.1 | BSD |
  | Pyphen | 0.18.1 | GPL-2.0+ / LGPL-2.1+ / MPL-1.1, tri-licensed, used under MPL-1.1 or LGPL |
  | Pillow | 13.0.0 | MIT-CMU |
  | fonttools[woff] | 4.68.0 | MIT; pulls brotli 1.2.1 (MIT) and zopfli 0.4.3 (Apache-2.0) |
  | brotlicffi | 1.2.1.0 | MIT; locked for non-CPython interpreters only, never installed here |
  | pypdf (dev) | 6.21.4 | BSD-3-Clause |

  `cffi` and `markupsafe` were already locked. Every compiled package has cp314 or abi3 wheels for manylinux x86_64 and macOS x86_64.
- **Debian packages:** `libpango-1.0-0`, `libpangoft2-1.0-0`, **`libharfbuzz-subset0`** and `fonts-dejavu-core` in `infrastructure/docker/api.Dockerfile` (the worker shares the image) and in the CI `api-unit` and `api-integration` jobs. `libharfbuzz-subset0` is added to the list above: without it WeasyPrint 70 emits a `DeprecationWarning` that it "will be required by future versions". `make check-pdf-image` renders a sample PDF inside the image, with warnings as errors.
- **Lazy import:** `weasyprint` is imported only inside the render function of `app/core/pdf.py`, so starting the API, exporting OpenAPI and running migrations need no Pango.
- **Code:** `app/core/i18n.py` (catalogs, `t`, `format_money`, `format_date`, `resolve_language`), `app/core/pdf.py` (the Jinja2 environment, WeasyPrint with a URL fetcher that refuses every URL) and the branded layout `app/templates/documents/base.html`.
- gettext and Babel are not added.

## Consequences

- **#98 (M6-13)** adds the dependencies above (or splits them into an infrastructure issue), `app/core/i18n.py`, the catalogs and their parity test, and the receipt templates in es and en.
- **M7 (payslips)** reuses the same templates, catalogs and formatting.
- **M8-04 (#121)** makes receipt templates editable, with the sandboxed Jinja2 environment.
- **M8-07 (#124)** generates large PDF exports in the worker, with the same rendering stack.
- One catalog format (JSON) for client and server; no extraction or compilation toolchain.
- The language of an issued document never changes when the recipient changes their preference later, because it is stored on the record.
- This ADR adds nothing to `pyproject.toml`, `uv.lock` or the Dockerfiles; #98 does.
- ISSUE-X1 is closed in architecture §10.

## Alternatives considered

- **E14 (b), gettext `.po`/`.mo` catalogs plus Babel, as ISSUE-X1 proposed.** Rejected: a second catalog format and toolchain (extraction, compilation) beside the web's JSON, and one more dependency, for two languages that share number formats.
- **E14 (c), ReportLab or another non-HTML PDF library.** Rejected: not the spec §10 choice, and layout code is harder to brand and review than HTML and CSS.

## References

- [`../architecture.md`](../architecture.md) §4.4, §7.3, §7.4, §10.12 ISSUE-X1, §11
- [`../spec.md`](../spec.md) §1, §6, §10 (application stack: i18n, reports)
- [ADR-0006](0006-financial-ledger-idempotency-and-numbering.md) (E13 receipts, E15 branding), [ADR-0001](0001-runtime-version-pins.md) (pin policy)
- GitHub issue #86 (this decision gate, item E14); #98, #121, #124; owner decision 2026-10-21 (files deferred)
