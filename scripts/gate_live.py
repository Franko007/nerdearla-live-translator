"""Gate final: ¿qué modelo Vertex live streama transcripción de forma fiable?

  A) gemini-3.5-transcribe-live-preview  (TEXT + input_audio_transcription)
  B) gemini-live-2.5-flash-native-audio   (sin modalities, solo input_audio_transcription)
  C) gemini-3.8-live                      (sin modalities + input_audio_transcription)

Mide, en N intentos: conexiones OK, transcripciones finales recibidas, y errores.
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

SAMPLE_RATE = 16000
CHUNK_BYTES = 3200


def load_pcm(path: str, seconds: int) -> bytes:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-t", str(seconds),
           "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def runner_config(modalities: list[str] | None) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=modalities,
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
    )


async def attempt(client, model: str, cfg, audio: bytes, seconds: int) -> tuple[bool, list[str], str]:
    """Devuelve (ok, transcripciones, error)."""
    texts: list[str] = []
    err = ""
    try:
        async with client.aio.live.connect(model=model, config=cfg) as session:
            async def sender():
                start = time.monotonic()
                pos = 0
                while pos < len(audio):
                    chunk = audio[pos:pos + CHUNK_BYTES]
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}"))
                    pos += len(chunk)
                    delay = (start + pos / (SAMPLE_RATE * 2)) - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                try:
                    await session.send_realtime_input(audio_stream_end=True)
                except Exception:
                    pass
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
                    break
                except Exception as e:
                    err = str(e).splitlines()[0][:140]
                    break
                n += 1
                sc = getattr(msg, "server_content", None)
                if sc is None:
                    continue
                for f in ("input_transcription", "interim_input_transcription"):
                    itx = getattr(sc, f, None)
                    if itx is not None and getattr(itx, "text", None):
                        texts.append(itx.text)
            task.cancel()
            return bool(texts), texts, err
    except Exception as e:
        return False, texts, f"{type(e).__name__}: {str(e).splitlines()[0][:140]}"


async def run_gate(attempts: int, seconds: int) -> None:
    client = genai.Client(vertexai=True, project=os.getenv("GOOGLE_CLOUD_PROJECT", "safeapp-b32af"),
                          location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    audio = load_pcm("samples/speech-tts-en.wav", seconds)
    cases = [
        ("A transcribe-live-preview", "publishers/google/models/gemini-3.5-transcribe-live-preview",
         runner_config(["TEXT"])),
        ("B flash-native-audio", "publishers/google/models/gemini-live-2.5-flash-native-audio",
         runner_config(None)),
        ("C 3.8-live", "publishers/google/models/gemini-3.8-live",
         runner_config(None)),
    ]
    for label, model, cfg in cases:
        ok = fails = errs = 0
        first_err = ""
        seen_final = False
        for i in range(attempts):
            if i:
                await asyncio.sleep(2)
            good, texts, err = await attempt(client, model, cfg, audio, seconds)
            if good:
                ok += 1
                if any(t and len(t) > 20 for t in texts):
                    seen_final = True
            elif err:
                fails += 1
                if not first_err:
                    first_err = err
            else:
                errs += 1
        print(f"[{label}] conectó+transcribió {ok}/{attempts} | final_largo={seen_final} | fails={fails} errs={errs}")
        if first_err:
            print(f"    primer error: {first_err}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--seconds", type=int, default=6)
    args = ap.parse_args()
    await run_gate(args.attempts, args.seconds)


if __name__ == "__main__":
    asyncio.run(main())