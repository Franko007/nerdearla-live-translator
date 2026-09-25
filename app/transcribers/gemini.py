"""Transcripción en vivo con Gemini Live API.

Convierte los eventos específicos de Gemini (server_content/input_transcription)
al formato normalizado del proyecto (TranscriptEvent).
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from app.audio.ffmpeg import SAMPLE_RATE
from app.models import TranscriptEvent


def build_gemini_client(settings):
    """Devuelve un cliente genai listo (API key o Vertex AI).

    El resto del sistema recibe este cliente y no sabe cómo se autenticó.
    """
    import google.genai as genai

    if settings.google_genai_use_vertexai:
        return genai.VertexAI(
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key)
    raise RuntimeError(
        "Falta GEMINI_API_KEY (o GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)."
    )


class GeminiTranscriber:
    GRACE_AFTER_AUDIO_S = 6.0

    def __init__(self, model: str, source_lang: str, *, client) -> None:
        self.model = model
        self.source_lang = source_lang
        self._client = client
        self.connected = False
        self.reconnections = 0
        self.error: str | None = None
        self.usage = None
        self._closed = False

    @property
    def status(self) -> dict:
        return {
            "connected": self.connected and not self._closed,
            "reconnections": self.reconnections,
            "error": self.error,
        }

    def _transcription_config(self):
        from google.genai import types

        lang_codes = [] if self.source_lang == "auto" else [self.source_lang]
        return types.AudioTranscriptionConfig(language_codes=lang_codes)

    def _connect_config(self):
        from google.genai import types

        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=self._transcription_config(),
        )

    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        """Consume audio PCM y emite TranscriptEvent normalizados.

        Reintenta la conexión con backoff 1s -> 2s -> 4s -> 8s y retoma el
        audio desde donde quedó. Un fallo de una sesión no afecta a las demás.
        """
        delay = 1.0
        while not self._closed:
            self.connected = True
            self.error = None
            async with self._client.aio.live.connect(model=self.model, config=self._connect_config()) as session:
                send = asyncio.create_task(self._send(session, audio))
                try:
                    drained_before_end = False
                    while True:
                        try:
                            async for msg in session.receive():
                                ev = self._normalize(msg)
                                if ev is not None:
                                    yield ev
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            self.error = f"{type(e).__name__}: {e}".split("\n")[0]
                            self.connected = False
                            raise _Disconnected from e

                        await asyncio.sleep(0.02)
                        if send.done():
                            # Audio terminó: esperar/consumir eventos finales tardíos.
                            if not drained_before_end:
                                await asyncio.sleep(self.GRACE_AFTER_AUDIO_S)
                                drained_before_end = True
                                continue
                            break
                    break  # flujo normal terminado
                except _Disconnected:
                    self.reconnections += 1
                    pass
                finally:
                    send.cancel()
                    try:
                        await send
                    except Exception:
                        pass
                if self._closed:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
        self.connected = False

    async def _send(self, session, audio: AsyncIterator[bytes]) -> None:
        from google.genai import types as gtypes

        while True:
            chunk = await anext(audio, b"")
            if not chunk:
                break
            await session.send_realtime_input(
                audio=gtypes.Blob(data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
            )
        try:
            await session.send_realtime_input(audio_stream_end=True)
        except Exception:
            pass

    def _normalize(self, msg) -> TranscriptEvent | None:
        um = getattr(msg, "usage_metadata", None)
        if um:
            self.usage = um
        sc = getattr(msg, "server_content", None)
        if sc is None:
            return None
        it = getattr(sc, "input_transcription", None)
        text = getattr(it, "text", None)
        if not text:
            return None
        finished = getattr(it, "finished", None)
        return TranscriptEvent(
            text=text,
            is_final=bool(finished),
            received_at=time.monotonic(),
        )

    async def close(self) -> None:
        self._closed = True