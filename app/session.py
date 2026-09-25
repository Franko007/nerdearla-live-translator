"""Sesión: una tarea aislada de procesamiento (audio -> transcripción -> traducción -> hub).

Cada sesión corre en una tarea propia; una sesión que falla no detiene a las demás.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from app.audio.ffmpeg import FfmpegSource
from app.config import Settings
from app.metrics import Metrics
from app.models import Segment, WireEvent
from app.providers import build_adapters
from app.replay import ReplayData, ReplayEvent, replay_path_for_audio
from app.segmenter import Segmenter

log = logging.getLogger("session")


class Session:
    def __init__(
        self,
        settings: Settings,
        hub,
        glossary: list[str],
        session_id: str,
        title: str,
        source_lang: str,
        target_langs: list[str],
        source: str,
        *,
        client=None,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.glossary = glossary
        self.id = session_id
        self.title = title or session_id
        self.source_lang = source_lang or settings.source_lang_default
        self.target_langs = target_langs or list(settings.target_langs)
        self.source = source
        self.client = client

        self.status = "created"          # created | running | finished | error | stopped
        self.error: str | None = None
        self.mode = "live"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.metrics = Metrics()
        self.segments: list[Segment] = []

        self.transcriber = None
        self.translator = None
        self._source: FfmpegSource | None = None
        self._stream = None          # WebSource (mic del navegador) si es captura en vivo
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.audio_started: float | None = None
        self._replay_events: list[ReplayEvent] = []

    @property
    def capturing(self) -> bool:
        """True cuando hay un mic de navegador alimentando la sesión."""
        return self._stream is not None and not self._stream.closed

    def attach_stream(self, stream) -> None:
        """Adjunta una fuente externa (WebSource) que reemplaza a FfmpegSource."""
        self._stream = stream

    # ------------------------------------------------------------------ utils
    def audio_clock_ms(self) -> int | None:
        """Posición actual del audio (proxy del pipeline). None si no empezó."""
        if self.audio_started is None:
            return None
        return int((time.monotonic() - self.audio_started) * 1000)

    def summary(self) -> dict:
        transcriber_info = {}
        if self.transcriber is not None and hasattr(self.transcriber, "status"):
            transcriber_info = self.transcriber.status
        tokens = {}
        if self.translator is not None and hasattr(self.translator, "usage_total"):
            tokens = self.translator.usage_total
        metrics = self.metrics.snapshot()
        metrics["reconnections"] = transcriber_info.get("reconnections", metrics["reconnections"])
        metrics["tokens_in"] = tokens.get("in", metrics["tokens_in"])
        metrics["tokens_out"] = tokens.get("out", metrics["tokens_out"])
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "mode": "LIVE" if self.mode == "live" else "DEMO",
            "live": self.mode == "live",
            "source_lang": self.source_lang,
            "target_langs": self.target_langs,
            "source": self.source,
            "capturing": self.capturing,
            "error": self.error,
            "created_at": self.created_at,
            "segments": len(self.segments),
            "metrics": metrics,
        }

    # ------------------------------------------------------------------ vida
    async def start(self) -> None:
        if self.status in ("starting", "running"):
            return
        self.status = "starting"
        self._task = asyncio.create_task(self._run(), name=f"session:{self.id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._source is not None:
            await self._source.close()
        if self._stream is not None:
            self._stream.close()
        if self.status in ("created", "starting", "running"):
            self.status = "stopped"

    async def _run(self) -> None:
        transcriber = translator = None
        try:
            transcriber, translator, mode = build_adapters(self.settings, self, self.glossary, self.client)
            self.transcriber = transcriber
            self.translator = translator
            self.mode = mode

            self.status = "running"
            self.audio_started = time.monotonic()
            segmenter = Segmenter(
                session_id=self.id,
                source_lang=self.source_lang,
                target_langs=self.target_langs,
                translator=translator,
                metrics=self.metrics,
                origin_s=self.audio_started,
            )
            self.segments = segmenter.segments
            if self._stream is not None:
                audio_iter = self._stream.chunks()
            else:
                self._source = FfmpegSource(self.source)
                audio_iter = self._source.chunks()
            log.info("sesión %s: iniciando pipeline (%s)", self.id, mode)
            async for ev in transcriber.stream(audio_iter):
                if self._stopping:
                    break
                ev.audio_pos_ms = self.audio_clock_ms()
                for wire in await segmenter.feed(ev, ev.audio_pos_ms):
                    await self._publish(wire)
            self.metrics.audio_ms = self.audio_clock_ms() or self.metrics.audio_ms
            self.status = "finished"
            log.info("sesión %s: terminó (%s segmentos)", self.id, len(self.segments))
        except asyncio.CancelledError:
            self.status = "stopped"
            raise
        except Exception as e:
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
            self.metrics.errors += 1
            log.error("sesión %s: ERROR %s", self.id, self.error)
        finally:
            if transcriber is not None:
                await transcriber.close()
            if self.settings.record:
                self._save_replay()

    async def _publish(self, wire: WireEvent) -> None:
        wire.session_id = self.id
        if not self.settings.is_replay and self.settings.record:
            if wire.kind in ("partial", "final"):
                self._replay_events.append(
                    ReplayEvent(
                        at_ms=self.audio_clock_ms() or 0,
                        kind=wire.kind,
                        text=wire.text,
                        start_ms=wire.start_ms,
                        end_ms=wire.end_ms,
                    )
                )
        await self.hub.publish(self.id, wire)

    def _save_replay(self) -> None:
        if not Path(self.source).is_file():
            log.warning("sesión %s: no se puede grabar replay, fuente no es un archivo local.", self.id)
            return
        path = replay_path_for_audio(self.source)
        data = ReplayData(
            session_id=self.id,
            title=self.title,
            source_lang=self.source_lang,
            target_langs=self.target_langs,
            audio=self.source,
            events=self._replay_events,
            segments=self.segments,
        )
        data.save(path)
        log.info("sesión %s: replay grabado en %s", self.id, path)