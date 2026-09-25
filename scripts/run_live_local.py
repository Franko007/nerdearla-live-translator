"""Sanity local: transcripción live Vertex sobre un archivo de audio.

Uso:
  python -m uv run python scripts/run_live_local.py [--audio samples/speech-tts-en.wav]

Requiere y usa la config del proyecto (PROVIDER=gemini, GOOGLE_GENAI_USE_VERTEXAI=true,
GOOGLE_CLOUD_PROJECT y GOOGLE_CLOUD_LOCATION=global) como si fuera una sesión real.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.transcribers.gemini import GeminiTranscriber, backend_model, build_gemini_client

SAMPLE_RATE = 16000
CHUNK_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms


def load_pcm(path: str) -> bytes:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path,
           "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


async def chunks_from_pcm(pcm: bytes):
    for pos in range(0, len(pcm), CHUNK_BYTES):
        yield pcm[pos:pos + CHUNK_BYTES]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default="samples/speech-tts-en.wav")
    ap.add_argument("--max-events", type=int, default=20)
    ap.add_argument("--chunked", action="store_true",
                    help="modo de emergencia por chunks vía generate_content "
                         "(funciona sin billing; elige TRANSLATE_MODEL como motor)")
    args = ap.parse_args()

    settings = Settings()
    print(f"backend: vertex={settings.google_genai_use_vertexai} key={'si' if settings.gemini_api_key else 'no'}", flush=True)
    client = build_gemini_client(settings)
    if args.chunked:
        model = backend_model(settings, settings.translate_model)
    else:
        model = backend_model(settings, settings.transcribe_model)
    print(f"model: {model} chunked={args.chunked}", flush=True)
    pcm = load_pcm(args.audio)
    print(f"audio: {args.audio} ({len(pcm) // (SAMPLE_RATE * 2):.1f}s)", flush=True)

    tr = GeminiTranscriber(model, settings.source_lang_default, client=client, chunked=args.chunked)
    t0 = time.monotonic()
    n = 0
    try:
        async for ev in tr.stream(chunks_from_pcm(pcm)):
            n += 1
            print(f"  [{time.monotonic() - t0:5.1f}s] final={ev.is_final} {ev.text[:90]!r}", flush=True)
            if n >= args.max_events:
                break
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
    print(f"fin  reconexiones={tr.reconnections} error={tr.error or '(ninguno)'}", flush=True)
    await tr.close()


if __name__ == "__main__":
    asyncio.run(main())