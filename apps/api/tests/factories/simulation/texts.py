"""The simulation's text pools (#543 E11 (a)): one module per language with
the same keys (``texts_es``, ``texts_en``), fixed strings and lists to choose
from. Faker is used only for person names (``plan.py``); no Faker sentence
or lorem anywhere.

:data:`RECEIPT_MESSAGES` are the business settings' two receipt messages
(``receipt_message_es`` and ``receipt_message_en``); both runs write both,
since a receipt is rendered in its recipient's language (ADR-0015).
"""

from typing import Final, Literal, cast

from tests.factories.simulation import texts_en, texts_es

type Language = Literal["es", "en"]
LANGUAGES: Final[tuple[Language, ...]] = ("es", "en")

POOLS: Final[dict[Language, dict[str, object]]] = {
    "es": texts_es.TEXTS,
    "en": texts_en.TEXTS,
}

RECEIPT_MESSAGES: Final[dict[Language, str]] = {
    "es": "Gracias por su pago. Conserve este recibo para cualquier aclaración.",
    "en": "Thank you for your payment. Please keep this receipt for your records.",
}

SAME_IN_BOTH_LANGUAGES: Final[frozenset[str]] = frozenset()
"""Proper nouns allowed to be identical in both pools (none so far)."""


class TextPool:
    """Typed access to one language's pool."""

    def __init__(self, language: Language) -> None:
        self.language = language
        self._texts = POOLS[language]

    def text(self, key: str) -> str:
        return cast(str, self._texts[key])

    def mapping(self, key: str) -> dict[str, str]:
        return cast(dict[str, str], self._texts[key])

    def choices(self, key: str) -> list[str]:
        return cast(list[str], self._texts[key])

    @property
    def center_name(self) -> str:
        return self.text("center_name")


def center_name(language: Language) -> str:
    return cast(str, POOLS[language]["center_name"])
