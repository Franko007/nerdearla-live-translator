"""Debug 5: flush de turno (send_client_content turn_complete=True) + transcribe no-live."""
from __future__ import annotations

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


async def probe_live_flush() -> None:
    """Envía audio realtime y cierra el turno con send_client_content."""
    model = os.getenv("TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    pcm = load_pcm("samples/speech-15s.mp3", 6)
    print(f"\n=== {model}: realtime + flush turno ===")
    cfg = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
    )
    try:
        async with client.aio.live.connect(model=model, config=cfg) as session:
            async def send_audio():
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
                await session.send_client_content(turns=[types.Content(parts=[types.Part(text="")])],
                                                  turn_complete=True)
                print("    [sender] audio + flush OK")
            task = asyncio.create_task(send_audio())
            it = session.receive()
            n = 0
            deadline = time.monotonic() + 16
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(it.__anext__(), timeout=1.2)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    print("    stream terminó")
                    break
                except Exception as e:
                    print(f"    receive ERROR: {type(e).__name__}: {e}")
                    break
                n += 1
                sc = getattr(msg, "server_content", None)
                itx = getattr(sc, "input_transcription", None) or getattr(sc, "interim_input_transcription", None)
                print(f"  msg[{n}]: " + (f"turn_complete={getattr(sc,'turn_complete',None)}" if sc and getattr(sc,'turn_complete',None) else str(msg)[:160]))
                if itx is not None and getattr(itx, "text", None):
                    print(f"      >> TEXTO: {itx.text!r} finished={getattr(itx,'finished',None)!r}")
            task.cancel()
            print(f"  total msgs: {n}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


def probe_transcribe() -> None:
    """Non-live gemini-3.5-transcribe con el sample completo (mp3 ya es audio)."""
    model = "gemini-3.5-transcribe"
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    print(f"\n=== {model} (generate_content) ===")
    media = types.Part.from_bytes(data=open("samples/speech-15s.mp3", "rb").read(),
                                  mime_type="audio/mp3")
    resp = client.models.generate_content(
        model=model,
        contents=types.Content(parts=[media, types.Part(text="Transcribe the audio.")]),
    )
    print("  texto:", resp.text[:300])
    print("  prompt_token_count:", resp.usage_metadata.prompt_token_count if resp.usage_metadata else None)
    print("  finish:", resp.finish_reason)


async def main() -> None:
    await probe_live_flush()
    probe_transcribe()


if __name__ == "__main__":
    asyncio.run(main())