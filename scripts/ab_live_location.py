"""A/B: modelo transcribe-live-preview en Vertex, regional vs global.

Mide tasa de transcripción y latencia para decidir en qué location quedarnos.
Patrón por intento: envío inmediato del PCM + audio_stream_end, receive con
timeout (evita colgarse si el server queda en silencio).

Uso:
  python -m uv run python scripts/ab_live_location.py --audio samples/speech-tts-en.wav --n 6
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types as gtypes

PROJECT = "safeapp-b32af"
ATTEMPT_TIMEOUT_S = 18


def _load_pcm(path: str, rate: int = 16000) -> bytes:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path,
           "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


async def attempt(client, model: str, seq: list[bytes], idx: int) -> str:
    cfg = gtypes.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=gtypes.AudioTranscriptionConfig(language_codes=["en-US"]),
    )
    texts: list[str] = []
    rcvd = 0
    t0 = time.monotonic()
    tag = f"{model.split('/')[-1]}"

    async def send():
        for c in seq:
            await sess.send_realtime_input(
                audio=gtypes.Blob(data=c, mime_type="audio/pcm;rate=16000")
            )
        await sess.send_realtime_input(audio_stream_end=True)

    try:
        async with client.aio.live.connect(model=model, config=cfg) as sess:
            task = asyncio.create_task(send())
            try:
                async with asyncio.timeout(ATTEMPT_TIMEOUT_S):
                    async for m in sess.receive():
                        rcvd += 1
                        sc = getattr(m, "server_content", None)
                        it = getattr(sc, "input_transcription", None) if sc else None
                        txt = getattr(it, "text", None)
                        if txt:
                            texts.append(txt)
                        if getattr(sc, "turn_complete", None):
                            break
            except TimeoutError:
                return f"{idx}: {tag} TIMEOUT (rcvd_msgs={rcvd})"
            task.cancel()
            try:
                await task
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001
        return f"{idx}: {tag} ERR {str(e).splitlines()[0][:70]}"
    ok = bool(texts and texts[-1])
    lat = time.monotonic() - t0
    if ok:
        return f"{idx}: {tag} OK txt={texts[-1][:50]!r} lat={lat:.1f}s"
    return f"{idx}: {tag} SIN-TEXTO (rcvd_msgs={rcvd}) lat={lat:.1f}s"


async def run(location: str, audio_path: str, n: int) -> None:
    client = genai.Client(vertexai=True, project=PROJECT, location=location)
    model = f"publishers/google/models/gemini-3.5-transcribe-live-preview"
    pcm = _load_pcm(audio_path)
    seq = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]
    print(f"=== location={location} model={model} n={n} audio={len(seq) * 0.1:.1f}s ===", flush=True)
    results = []
    for i in range(n):
        r = await attempt(client, model, seq, i)
        print("  " + r, flush=True)
        results.append(r)
        await asyncio.sleep(1)
    oks = [r for r in results if "OK txt=" in r]
    print(f"--> {location}: {len(oks)}/{n} con texto", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default="samples/speech-tts-en.wav")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--locations", nargs="*", default=["us-central1", "global"])
    args = ap.parse_args()
    for loc in args.locations:
        await run(loc, args.audio, args.n)


if __name__ == "__main__":
    asyncio.run(main())