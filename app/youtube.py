"""Resolución de URLs de YouTube a streams de audio directos via yt-dlp.

Si yt-dlp no está disponible o la URL no es de YouTube, devuelve la URL tal cual.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from functools import partial

log = logging.getLogger(__name__)

_YT_PATTERN = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?.*v=|live/|shorts/|embed/)|youtu\.be/)"
    r"[\w-]+"
)


def is_youtube_url(url: str) -> bool:
    return bool(_YT_PATTERN.search(url))


def _resolve_sync(url: str) -> str:
    """Devuelve la URL directa de audio con yt-dlp (módulo Python; si no, CLI)."""
    try:
        import yt_dlp  # noqa: PLC0415

        opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        direct_url = _best_audio_url(info)
        if direct_url:
            return direct_url
        raise RuntimeError("yt-dlp no devolvió ninguna URL de audio.")
    except ImportError:
        pass

    if shutil.which("yt-dlp") is None:
        raise RuntimeError("yt-dlp no está instalado (módulo Python) ni se encontró el binario 'yt-dlp'.")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestaudio/best",
        "--get-url",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        err = result.stderr.strip()
        raise RuntimeError(f"yt-dlp falló: {err}")
    direct_url = result.stdout.strip().splitlines()[0]
    if not direct_url:
        raise RuntimeError("yt-dlp no devolvió ninguna URL.")
    return direct_url


def _best_audio_url(info: dict) -> str | None:
    """Elige el formato de audio de `info['formats']` (o el url directo)."""
    if info is None:
        return None
    formats = info.get("formats") or []
    audio = [f for f in formats if f.get("acodec") not in (None, "none")]
    audio.sort(key=lambda f: (f.get("abr") or 0), reverse=True)
    chosen = audio[0] if audio else (formats[-1] if formats else info)
    return chosen.get("url")


async def resolve_source(url: str) -> tuple[str, str | None]:
    """Devuelve (url_resuelta, advertencia|None).

    Si es una URL de YouTube, la resuelve con yt-dlp (módulo Python o binario).
    Si yt-dlp no está disponible, devuelve la URL original con una advertencia.
    """
    if not is_youtube_url(url):
        return url, None

    log.info("Resolviendo URL de YouTube con yt-dlp: %s", url)
    loop = asyncio.get_event_loop()
    try:
        resolved = await loop.run_in_executor(None, partial(_resolve_sync, url))
        log.info("URL resuelta: %s…", resolved[:80])
        return resolved, None
    except Exception as exc:  # noqa: BLE001
        log.warning("yt-dlp falló (%s); usando URL original.", exc)
        return url, f"yt-dlp falló: {exc}. Intentando con la URL original."
