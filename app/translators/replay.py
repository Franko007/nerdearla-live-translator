"""Traducción mediante grabaciones registradas de una corrida real.

Resuelve (source_text, target_lang) -> translated_text. Si no hay traducción
registrada, devuelve el texto original (fallback honesto).
"""
from __future__ import annotations

from app.replay import ReplayData


class ReplayTranslator:
    def __init__(self, data: ReplayData) -> None:
        self._map = data.translations
        self.calls = 0
        self.hits = 0

    async def translate(self, source_text: str, target_lang: str) -> str:
        self.calls += 1
        text = self._map.get((source_text, target_lang))
        if text is not None:
            self.hits += 1
            return text
        return source_text