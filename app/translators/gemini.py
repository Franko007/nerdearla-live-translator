"""Traducción con Gemini + glosario.

El glosario se inyecta como instrucción de sistema: el modelo debe conservar
los términos técnicos tal como están escritos.
"""
from __future__ import annotations


from app.transcribers.gemini import _call_with_retry


class GeminiTranslator:
    def __init__(self, model: str, glossary: list[str], *, client) -> None:
        self.model = model
        self.glossary = glossary
        self._client = client
        self.usage_total = {"in": 0, "out": 0}
        self.calls = 0
        self.error: str | None = None

    async def translate(self, source_text: str, target_lang: str) -> str:
        from google.genai import types

        system = (
            f"You are a live-caption translator. Translate the user's text into {target_lang}. "
            "Output ONLY the translation, with no comments or quotes."
        )
        if self.glossary:
            system += " Keep these terms exactly as written: " + ", ".join(self.glossary[:100]) + "."

        self.calls += 1
        resp = await _call_with_retry(
            lambda: self._client.aio.models.generate_content(
                model=self.model,
                contents=source_text,
                config=types.GenerateContentConfig(system_instruction=system),
            )
        )
        um = getattr(resp, "usage_metadata", None)
        self.usage_total["in"] += getattr(um, "prompt_token_count", 0) or 0
        self.usage_total["out"] += getattr(um, "candidates_token_count", 0) or 0
        text = (resp.text or "").strip()
        if not text:
            self.error = "Gemini respondió una traducción vacía."
            return ""
        return text