"""Spike Vertex: gate de decisión live-vs-chunks.

Probas en 3 pasos contra Vertex AI (proyecto safeapp-b32af):
  1. generate_content flash-lite sobre voz real -> verifica creds E2E.
  2. Live transcribe-live (WebSocket) -> decide si hay transcripción live.
  3. Live native-audio (WebSocket bidi) -> intento alternativo.

Uso:  python -m uv run python scripts/spike_vertex.py [--seconds 6]
Requiere ADC: gcloud auth application-default login
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "safeapp-b32af")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
SAMPLE_RATE = 16000
CHUNK_BYTES = 3200


def load_pcm(path: str, seconds: int) -> bytes:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-t", str(seconds),
           "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def build_client(vertex: bool) -> genai.Client:
    if vertex:
        return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def model_name(vertex: bool, short: str) -> str:
    """Vertex usa ruta completa y sufijos distintos; AI Studio usa el nombre corto."""
    if not vertex:
        return short
    return f"publishers/google/models/{short}"


# --------------------------------------------------------------- paso 1
def step1_transcribe_rest(vertex: bool) -> None:
    """generate_content: flash-lite transcribe la voz TTS."""
    label = "Vertex" if vertex else "AI Studio"
    print(f"\n=== Paso 1: flash-lite transcribe (via {label}) ===")
    try:
        client = build_client(vertex)
    except Exception as e:
        print(f"  build_client ERROR: {type(e).__name__}: {e}")
        return False
    wav = open("samples/speech-tts-en.wav", "rb").read()
    media = types.Part.from_bytes(data=wav, mime_type="audio/wav")
    t0 = time.monotonic()
    try:
        resp = client.models.generate_content(
            model=model_name(vertex, "gemini-3.5-flash-lite"),
            contents=types.Content(parts=[media, types.Part(text="Transcribe verbatim the speech. Output only the text.")]),
        )
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {str(e).splitlines()[0][:120]}")
        return False
    dt = time.monotonic() - t0
    ok = bool(resp.text) and "noise" not in resp.text.lower()
    print(f"  {'OK ' if ok else 'FALLO'} {dt*1000:.0f}ms | {resp.text!r}")
    return ok


# --------------------------------------------------------------- pasos 2-3
async def probe_live(vertex: bool, model: str, seconds: int) -> None:
    label = "Vertex" if vertex else "AI Studio"
    print(f"\n=== {'Paso 3' if 'native-audio' in model else 'Paso 2'}: live {model} (via {label}) ===")
    try:
        client = build_client(vertex)
    except Exception as e:
        print(f"  build_client ERROR: {type(e).__name__}: {e}")
        return
    pcm = load_pcm("samples/speech-tts-en.wav", seconds)
    cfg = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
    )
    m = model_name(vertex, model)
    try:
        async with client.aio.live.connect(model=m, config=cfg) as session:
            async def sender():
                start = time.monotonic()
                pos = 0
                while pos < len(pcm):
                    chunk = pcm[pos:pos + CHUNK_BYTES]
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}"))
                    pos += len(chunk)
                    delay = (start + pos / (SAMPLE_RATE * 2)) - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                await session.send_realtime_input(audio_stream_end=True)
            task = asyncio.create_task(sender())
            it = session.receive()
            n = 0
            deadline = time.monotonic() + seconds + 10
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(it.__anext__(), timeout=1.5)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    print("  stream terminó")
                    break
                except Exception as e:
                    print(f"  receive ERROR: {type(e).__name__}: {e}")
                    break
                n += 1
                sc = getattr(msg, "server_content", None)
                itx = getattr(sc, "input_transcription", None) if sc else None
                if itx is not None and getattr(itx, "text", None):
                    print(f"  msg{n}: TRANSCRIPCION {itx.text!r} final={getattr(itx,'finished',None)!r}")
                elif sc is not None and getattr(sc, "input_transcription", None):
                    print(f"  msg{n}: (transcripción vacía)")
                else:
                    print(f"  msg{n}: {str(msg)[:150]}")
            task.cancel()
            print(f"  total msgs: {n}")
    except Exception as e:
        cause = getattr(e, "message", None) or str(e)
        print(f"  connect ERROR: {type(e).__name__}: {cause}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--api-key", action="store_true", help="probar también contra AI Studio")
    args = ap.parse_args()

    for vertex in (True, args.api_key):
        try:
            step1_transcribe_rest(vertex)
        except Exception as e:
            print(f"  step1 ERROR: {type(e).__name__}: {str(e).splitlines()[0][:120]}")

    # Gate live en Vertex (proyecto habilitado: gemini-3.5-transcribe-live-preview
    # y gemini-live-2.5-flash-native-audio). AI Studio: los nombres de AI Studio.
    for vertex in (True, args.api_key):
        models = (
            ["gemini-3.5-transcribe-live-preview", "gemini-3.8-live", "gemini-live-2.5-flash-native-audio"]
            if vertex else
            ["gemini-3.5-transcribe-live", "gemini-2.5-flash-native-audio-latest"]
        )
        for model in models:
            await probe_live(vertex, model, args.seconds)


if __name__ == "__main__":
    asyncio.run(main())