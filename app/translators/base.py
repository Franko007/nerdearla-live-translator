"""Contrato mínimo de traducción."""
from __future__ import annotations

from typing import Protocol


class Translator(Protocol):
    async def translate(self, source_text: str, target_lang: str) -> str:
        """Traduce source_text al idioma target_lang."""
        ...