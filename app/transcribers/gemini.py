"""Transcripción en vivo con Gemini Live API (o por chunks vía AI Studio).

Convierte los eventos específicos de Gemini (server_content/input_transcription)
al formato normalizado del proyecto (TranscriptEvent).

Modo por chunks (fallback sin billing):
  - No usa la Live API; parte el audio en ventanas y llama generate_content.
  - Funciona en AI Studio con GEMINI_API_KEY (y también en Vertex si hay 3.x).
  - Más latente, pero no requiere billing de GCP.
"""
from __future__ import annotations

import asyncio
import io
import time
import wave
from typing import AsyncIterator

from app.audio.ffmpeg import SAMPLE_RATE
from app.models import TranscriptEvent

# Fallback: ventana de audio por llamada y prompt de transcripción por chunks.
CHUNK_MS = 4000
CHUNK_PROMPT = "Transcribe verbatim the speech in the audio. Output only the transcribed text, no commentary."


def build_gemini_client(settings):
    """Devuelve un cliente genai listo (API key o Vertex AI).

    El resto del sistema recibe este cliente y no sabe cómo se autenticó.
    """
    import google.genai as genai

    if settings.google_genai_use_vertexai:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key)
    raise RuntimeError(
        "Falta GEMINI_API_KEY (o GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)."
    )


# Nombres que difieren entre AI Studio y Vertex AI (backend del proyecto).
_VERTEX_RENAME = {
    "gemini-3.5-transcribe-live": "gemini-3.5-transcribe-live-preview",
}


class _Disconnected(Exception):
    """Señal interna: la sesión live cayó y hay que reconectar con backoff."""


def backend_model(settings, short_name: str) -> str:
    """Convierte el nombre corto al nombre real del backend (Vertex usa ruta completa)."""
    if not settings.google_genai_use_vertexai:
        return short_name
    name = _VERTEX_RENAME.get(short_name, short_name)
    return f"publishers/google/models/{name}"


class GeminiTranscriber:
    GRACE_AFTER_AUDIO_S = 6.0

    def __init__(self, model: str, source_lang: str, *, client, chunked: bool = False, chunk_ms: int = CHUNK_MS) -> None:
        self.model = model
        self.source_lang = source_lang
        self._client = client
        self.chunked = chunked
        self.chunk_ms = chunk_ms
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

        En modo `chunked` (fallback sin billing) el audio se transcribe por
        ventanas con generate_content; si no, se usa la Live API con
        reconexión con backoff 1s -> 2s -> 4s -> 8s y retoma del audio donde
        quedó. Un fallo de una sesión no afecta a las demás. Cuando el audio ya
        se agotó, corta la reconexión y termina.
        """
        if self.chunked:
            async for ev in self.stream_chunked(audio):
                yield ev
            return

        delay = 1.0
        while not self._closed:
            self.connected = True
            self.error = None
            send: asyncio.Task | None = None
            try:
                async with self._client.aio.live.connect(model=self.model, config=self._connect_config()) as session:
                    send = asyncio.create_task(self._send(session, audio))
                    it = session.receive()
                    drained = False
                    try:
                        while True:
                            try:
                                msg = await asyncio.wait_for(it.__anext__(), timeout=1.0)
                            except asyncio.TimeoutError:
                                if send.done():
                                    if not drained:
                                        await asyncio.sleep(self.GRACE_AFTER_AUDIO_S)
                                        drained = True
                                    else:
                                        break
                                continue
                            except StopAsyncIteration:
                                break
                            except Exception as e:
                                self.error = f"{type(e).__name__}: {e}".split("\n")[0]
                                self.connected = False
                                raise _Disconnected from e
                            ev = self._normalize(msg)
                            if ev is not None:
                                yield ev
                    finally:
                        send.cancel()
                        try:
                            await send
                        except (asyncio.CancelledError, Exception):
                            pass
                if not send.done():
                    # El stream se cerró con audio en curso: el backend cortó la
                    # sesión (a veces sin eventos). Hay que reconectar.
                    self.connected = False
                    self.error = "stream cerrado por el backend sin terminación"
                    raise _Disconnected
                break  # flujo normal terminado
            except _Disconnected:
                if send is not None and send.done():
                    # El audio ya se envió entero: no hay nada más que mandar.
                    break
                self.reconnections += 1
            if self._closed:
                break
            if self.connected:
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

    # --------------------------------------------------------- modo chunks
    def _window_bytes(self) -> int:
        return int(SAMPLE_RATE * 2 * self.chunk_ms / 1000)

    @staticmethod
    def _pcm_to_wav(pcm: bytes) -> bytes:
        """Envuelve PCM s16le mono al contenedor WAV que espera generate_content."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    async def stream_chunked(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        """Parte el audio en ventanas de `chunk_ms` y transcribe cada una.

        Emite un TranscriptEvent final por ventana con su rango de tiempo real
        (start_ms/end_ms relativos al audio), que el Segmenter convierte en
        segmento y traduce directo.
        """
        buf = bytearray()
        window_bytes = self._window_bytes()
        start_ms = 0
        while not self._closed:
            chunk = await anext(audio, b"")
            if not chunk:
                if buf:
                    yield await self._transcribe_window(bytes(buf), start_ms)
                break
            buf += chunk
            if len(buf) >= window_bytes:
                yield await self._transcribe_window(bytes(buf), start_ms)
                start_ms += self.chunk_ms
                buf.clear()

    def _chunk_prompt(self) -> str:
        if self.source_lang and self.source_lang != "auto":
            return f"{CHUNK_PROMPT}\nThe speech is in the language code: {self.source_lang}."
        return CHUNK_PROMPT

    async def _transcribe_window(self, pcm: bytes, start_ms: int) -> TranscriptEvent:
        from google.genai import types

        self.connected = True
        media = types.Part.from_bytes(data=self._pcm_to_wav(pcm), mime_type="audio/wav")
        prompt = types.Part(text=self._chunk_prompt())
        try:
            resp = await self._client.aio.models.generate_content(
                model=self.model,
                contents=types.Content(parts=[media, prompt]),
            )
        except Exception as e:
            self.connected = False
            self.error = f"{type(e).__name__}: {e}".split("\n")[0]
            raise
        text = (resp.text or "").strip()
        if not text:
            self.error = "generate_content devolvió transcripción vacía."
            text = ""
        return TranscriptEvent(
            text=text,
            is_final=True,
            start_ms=start_ms,
            end_ms=start_ms + self.chunk_ms,
            received_at=time.monotonic(),
            audio_pos_ms=start_ms + self.chunk_ms,
        )

    async def close(self) -> None:
        self._closed = True