"""SessionManager: creación/parada/listado de sesiones.

El MVP mantiene el estado en memoria y corre en un único proceso.
Los límites (MAX_SESSIONS) son de proceso, no de producto.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.config import Settings
from app.hub import Hub
from app.session import Session


_MAX_ID_LEN = 30


class SessionManager:
    def __init__(self, settings: Settings, hub: Hub, glossary: list[str], client=None) -> None:
        self.settings = settings
        self.hub = hub
        self.glossary = glossary
        self.client = client
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._counter = 0

    # ------------------------------------------------------------- consultas
    def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self.sessions.values())

    def summaries(self) -> list[dict]:
        return [s.summary() for s in sorted(self.sessions.values(), key=lambda s: s.created_at)]

    # -------------------------------------------------------------- creación
    async def create(
        self,
        source: str,
        *,
        session_id: str | None = None,
        title: str = "",
        source_lang: str | None = None,
        target_langs: list[str] | None = None,
        auto_start: bool = True,
    ) -> Session:
        if path := self._resolve_local_path(source):
            if not path.is_file():
                raise ValueError(f"Fuente de audio no existe: {source}")

        async with self._lock:
            if len(self.sessions) >= self.settings.max_sessions:
                raise RuntimeError(f"Límite de sesiones alcanzado ({self.settings.max_sessions}).")
            sid = self._make_id(source, title, session_id)
            if sid in self.sessions:
                raise ValueError(f"Ya existe una sesión con id '{sid}'.")

            session = Session(
                settings=self.settings,
                hub=self.hub,
                glossary=self.glossary,
                session_id=sid,
                title=title,
                source_lang=source_lang or "",
                target_langs=target_langs,
                source=str(path) if path else source,
                client=self.client,
            )
            self.sessions[sid] = session

        if auto_start:
            await session.start()
        return session

    async def stop(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        await session.stop()
        return True

    async def remove(self, session_id: str) -> bool:
        async with self._lock:
            if session_id not in self.sessions:
                return False
            self.sessions.pop(session_id, None)
            return True

    # ---------------------------------------------------------- auto discover
    def discover_samples(self) -> list[Session]:
        """Modo Replay: crea una sesión por cada samples/*.wav con su .replay.json."""
        from app.replay import load_replay

        created: list[Session] = []
        samples_dir: Path = self.settings.samples_dir
        if not samples_dir.is_dir():
            return created
        for wav in sorted(samples_dir.glob("*.wav")):
            replay = wav.with_suffix(".replay.json")
            if not replay.is_file():
                continue
            data = load_replay(replay)
            session = Session(
                settings=self.settings,
                hub=self.hub,
                glossary=self.glossary,
                session_id=data.session_id,
                title=data.title,
                source_lang=data.source_lang,
                target_langs=list(data.target_langs),
                source=str(wav),
                client=self.client,
            )
            self.sessions[session.id] = session
            created.append(session)
        return created

    # ---------------------------------------------------------------- helpers
    def _resolve_local_path(self, source: str) -> Path | None:
        if source.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://", "mic://")):
            return None
        return Path(source)

    @staticmethod
    def _make_id(source: str, title: str, session_id: str | None) -> str:
        if session_id:
            sid = re.sub(r"[^a-zA-Z0-9_-]", "-", session_id).strip("-")
            if not sid:
                raise ValueError("session_id inválido.")
            return sid[: _MAX_ID_LEN]
        base = re.sub(r"[^a-zA-Z0-9_-]", "-", title.lower()).strip("-")
        if not base:
            base = Path(source).stem or "session"
        return base[: _MAX_ID_LEN]