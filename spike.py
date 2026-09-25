#!/usr/bin/env python3
"""
spike.py - Validación rápida de Gemini ANTES de construir el pipeline.

Responde, con datos reales de tu cuenta:
  1. ¿Existe el modelo de transcripción en vivo y tu API key tiene acceso?
  2. ¿Cómo llegan los eventos? (¿parcial/final? ¿texto acumulado o incremental?)
  3. ¿Cuánto tarda el primer texto?
  4. ¿Funciona el idioma automático / fijo? ¿El SDK acepta custom_vocabulary?
  5. ¿Funcionan 2 conexiones simultáneas?
  6. ¿Cuánto tarda y cuánto cuesta (tokens) traducir con Flash-Lite?

Uso:
  pip install google-genai python-dotenv
  # API key (elegí según tu sistema):
  #   Mac/Linux:   export GEMINI_API_KEY="tu_clave"
  #   PowerShell:  $env:GEMINI_API_KEY="tu_clave"
  #   CMD:         set GEMINI_API_KEY=tu_clave
  # o ponela en un archivo .env

  python spike.py --translate-only                        # prueba solo la traducción
  python spike.py --audio samples/en.mp3 --seconds 45     # 1 sesión
  python spike.py --audio samples/en.mp3 samples/es.mp3   # 2 sesiones simultáneas
  python spike.py --audio samples/en.mp3 --verbose        # muestra los eventos crudos

Requiere ffmpeg instalado y en el PATH.
Los modelos se pueden cambiar sin tocar el código:
  TRANSCRIBE_MODEL=... TRANSLATE_MODEL=... python spike.py ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    sys.exit("Falta la librería. Instalá con:  pip install google-genai python-dotenv")

SAMPLE_RATE = 16000                      # PCM 16-bit, 16 kHz, mono, little-endian
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000   # 32 bytes por milisegundo
CHUNK_MS = 100
CHUNK_BYTES = BYTES_PER_MS * CHUNK_MS    # 3200 bytes por chunk de 100 ms

TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "gemini-3.5-flash-lite")

SAMPLE_TEXT = (
    "Hello everyone, and welcome to Nerdearla. Today we are going to talk about "
    "Kubernetes and how to run observability at scale. We will use OpenTelemetry, "
    "Prometheus and Grafana. If you deploy on Cloud Run, the first step is a good "
    "health check. Let's get started."
)


@dataclass
class SessionStats:
    label: str
    audio_ms_total: int = 0
    audio_sent_ms: int = 0
    connected: bool = False
    events: list = field(default_factory=list)
    first_event_s: float | None = None
    kinds: Counter = field(default_factory=Counter)
    flags: Counter = field(default_factory=Counter)
    cumulative_hits: int = 0
    prev_text: str = ""
    usage: object = None
    vocab_status: str = "no probado (sin --glossary)"
    error: str | None = None


# ----------------------------------------------------------------------------
# Audio
# ----------------------------------------------------------------------------
def load_pcm(path: str, seconds: int) -> bytes:
    """Convierte cualquier audio a PCM 16 kHz mono con ffmpeg."""
    cmd = [
        "ffmpeg", "-loglevel", "error", "-i", path, "-t", str(seconds),
        "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
    ]
    try:
        return subprocess.run(cmd, capture_output=True, check=True).stdout
    except FileNotFoundError:
        sys.exit("ffmpeg no está instalado o no está en el PATH. Probá:  ffmpeg -version")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ffmpeg falló con {path}: {e.stderr.decode(errors='ignore')}")


async def sender(session, pcm: bytes, st: SessionStats) -> None:
    """Emite el audio a ritmo real (100 ms de audio cada 100 ms) para simular un escenario en vivo."""
    start = time.monotonic()
    pos = 0
    while pos < len(pcm):
        chunk = pcm[pos:pos + CHUNK_BYTES]
        await session.send_realtime_input(
            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
        )
        pos += len(chunk)
        st.audio_sent_ms = pos // BYTES_PER_MS
        delay = (start + st.audio_sent_ms / 1000) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    try:
        await session.send_realtime_input(audio_stream_end=True)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Eventos de transcripción
# ----------------------------------------------------------------------------
def handle_message(msg, st: SessionStats, t0: float, verbose: bool) -> None:
    now = time.monotonic() - t0
    um = getattr(msg, "usage_metadata", None)
    if um:
        st.usage = um

    sc = getattr(msg, "server_content", None)
    if sc is None:
        if verbose:
            print(f"[{st.label}] mensaje sin server_content: {str(msg)[:200]}")
        return

    # ¿Qué señales trae el servidor? (turn_complete, generation_complete, etc.)
    for name in type(sc).model_fields:
        if name in ("input_transcription", "output_transcription", "model_turn"):
            continue
        val = getattr(sc, name, None)
        if val not in (None, False, [], ""):
            st.flags[name] += 1

    it = getattr(sc, "input_transcription", None)
    text = getattr(it, "text", None) if it else None
    if not text:
        return

    finished = getattr(it, "finished", None)
    kind = "final" if finished is True else "partial" if finished is False else "texto"

    if st.prev_text and text.startswith(st.prev_text):
        st.cumulative_hits += 1
    st.prev_text = text

    if st.first_event_s is None:
        st.first_event_s = now
    st.kinds[kind] += 1
    st.events.append({
        "at_ms": int(now * 1000),
        "audio_sent_ms": st.audio_sent_ms,
        "kind": kind,
        "text": text,
    })
    print(f"[{st.label}] +{now:5.1f}s (audio enviado {st.audio_sent_ms / 1000:5.1f}s) {kind:8s} {text!r}")
    if verbose:
        print(f"[{st.label}]   crudo: {it!r}")


async def receiver(session, st: SessionStats, t0: float, verbose: bool) -> None:
    """receive() termina en cada turn_complete: hay que volver a llamarlo."""
    while True:
        try:
            async for msg in session.receive():
                handle_message(msg, st, t0, verbose)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            st.error = f"receive(): {type(e).__name__}: {e}"
            return
        await asyncio.sleep(0.05)


async def run_session(client, st: SessionStats, pcm: bytes, lang: str,
                      vocab: list[str], verbose: bool, grace: float) -> None:
    st.audio_ms_total = len(pcm) // BYTES_PER_MS
    lang_codes = [] if lang == "auto" else [lang]

    try:
        if vocab:
            tcfg = types.AudioTranscriptionConfig(language_codes=lang_codes, custom_vocabulary=vocab)
            st.vocab_status = "el SDK lo acepta (falta confirmar que el servidor lo respete)"
        else:
            tcfg = types.AudioTranscriptionConfig(language_codes=lang_codes)
    except Exception as e:
        st.vocab_status = (f"el SDK NO lo acepta ({type(e).__name__}) -> "
                           "usar el glosario solo en el prompt del traductor")
        tcfg = types.AudioTranscriptionConfig(language_codes=lang_codes)

    cfg = types.LiveConnectConfig(response_modalities=["TEXT"], input_audio_transcription=tcfg)
    t0 = time.monotonic()
    try:
        async with client.aio.live.connect(model=TRANSCRIBE_MODEL, config=cfg) as session:
            st.connected = True
            print(f"[{st.label}] conectado a {TRANSCRIBE_MODEL} (idioma: {lang})")
            rx = asyncio.create_task(receiver(session, st, t0, verbose))
            await sender(session, pcm, st)
            await asyncio.sleep(grace)  # esperar eventos finales que llegan tarde
            rx.cancel()
            try:
                await rx
            except asyncio.CancelledError:
                pass
    except Exception as e:
        st.error = f"{type(e).__name__}: {e}"
        print(f"[{st.label}] ERROR: {st.error}")


def build_transcript(st: SessionStats) -> tuple[str, str]:
    texts = [e["text"] for e in st.events]
    if not texts:
        return "", "sin eventos"
    if st.cumulative_hits > 0.6 * len(texts):
        out, prev = [], ""
        for t in texts:
            if prev and not t.startswith(prev):
                out.append(prev)
            prev = t
        out.append(prev)
        return re.sub(r"\s+", " ", " ".join(out)).strip(), "ACUMULADO (cada evento repite el texto anterior + más)"
    return re.sub(r"\s+", " ", "".join(texts)).strip(), "INCREMENTAL (cada evento trae solo texto nuevo)"


# ----------------------------------------------------------------------------
# Traducción
# ----------------------------------------------------------------------------
async def translation_test(client, text: str, target: str, glossary: list[str], n: int = 5) -> dict:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if len(sentences) < 2 and len(text) > 200:  # sin puntuación: cortar por largo
        sentences = [text[i:i + 140] for i in range(0, len(text), 140)]
    sentences = sentences[:n]

    system = (f"You are a live-caption translator. Translate the user's text into {target}. "
              "Output ONLY the translation, with no comments or quotes.")
    if glossary:
        system += " Keep these terms exactly as written: " + ", ".join(glossary[:100]) + "."

    result = {"latencies_s": [], "in_tokens": 0, "out_tokens": 0, "pairs": []}
    for s in sentences:
        t = time.monotonic()
        try:
            resp = await client.aio.models.generate_content(
                model=TRANSLATE_MODEL,
                contents=s,
                config=types.GenerateContentConfig(system_instruction=system),
            )
        except Exception as e:
            print(f"[traducción] ERROR: {type(e).__name__}: {e}")
            result["error"] = f"{type(e).__name__}: {e}"
            break
        dt = time.monotonic() - t
        um = getattr(resp, "usage_metadata", None)
        result["latencies_s"].append(dt)
        result["in_tokens"] += getattr(um, "prompt_token_count", 0) or 0
        result["out_tokens"] += getattr(um, "candidates_token_count", 0) or 0
        result["pairs"].append((s, (resp.text or "").strip()))
        print(f"[traducción] {dt:4.2f}s  EN: {s}\n{'':22}-> {(resp.text or '').strip()}")
    return result


# ----------------------------------------------------------------------------
# Resumen
# ----------------------------------------------------------------------------
def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def print_summary(stats: list[SessionStats], tr: dict | None) -> None:
    print("\n" + "=" * 72)
    print("RESUMEN DEL SPIKE")
    print("=" * 72)
    print(f"Modelo de transcripción: {TRANSCRIBE_MODEL}")
    print(f"Modelo de traducción:    {TRANSLATE_MODEL}\n")

    for st in stats:
        transcript, mode = build_transcript(st)
        audio_s = st.audio_ms_total / 1000 or 1
        print(f"--- Sesión {st.label} ---")
        print(f"  Conexión OK:              {'sí' if st.connected else 'NO'}")
        print(f"  Error:                    {st.error or 'ninguno'}")
        print(f"  Eventos de texto:         {len(st.events)}  {dict(st.kinds)}")
        print(f"  Primer texto a los:       "
              f"{'-' if st.first_event_s is None else f'{st.first_event_s:.2f}s'} (incluye el tiempo hasta que empieza a hablar)")
        print(f"  Formato de los eventos:   {mode}")
        print(f"  ¿Marca final (finished)?: {'sí' if st.kinds.get('final') else 'NO se observó'}")
        print(f"  Otras señales del server: {dict(st.flags) or 'ninguna'}")
        print(f"  Vocabulario/glosario:     {st.vocab_status}")
        if st.usage is not None:
            total = getattr(st.usage, "total_token_count", None)
            print(f"  Uso reportado:            {st.usage}")
            if total:
                print(f"  Tokens por hora de audio: ~{int(total * 3600 / audio_s):,} "
                      "(multiplicar por el precio vigente del modelo)")
        else:
            print("  Uso reportado:            el servidor no envió usage_metadata")
        print(f"  Audio procesado:          {audio_s:.0f}s\n")

    if tr:
        lat = tr["latencies_s"]
        print("--- Traducción ---")
        if lat:
            print(f"  Llamadas: {len(lat)}  p50: {pct(lat, .5):.2f}s  p95: {pct(lat, .95):.2f}s")
            print(f"  Tokens: entrada {tr['in_tokens']}, salida {tr['out_tokens']}")
        if tr.get("error"):
            print(f"  Error: {tr['error']}")

    ok = [s for s in stats if s.connected and s.events]
    if len(stats) >= 2:
        print(f"\nDOS CONEXIONES SIMULTÁNEAS: {'OK' if len(ok) == len(stats) else 'FALLÓ - revisar errores arriba'}")
    print("\nPegame este resumen completo para pasar a la Fase 2.")


def save_events(stats: list[SessionStats]) -> None:
    out = Path("spike_out")
    out.mkdir(exist_ok=True)
    for st in stats:
        (out / f"{st.label}.events.json").write_text(
            json.dumps({"label": st.label, "audio_ms": st.audio_ms_total, "events": st.events},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"Eventos guardados en {out}/ (sirven de base para el modo replay).")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
async def amain(args) -> None:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        sys.exit("Falta GEMINI_API_KEY (variable de entorno o archivo .env).")
    client = genai.Client(api_key=key)

    glossary: list[str] = []
    if args.glossary:
        glossary = [l.strip() for l in Path(args.glossary).read_text(encoding="utf-8").splitlines() if l.strip()]

    if args.translate_only:
        print(f"Probando solo traducción con {TRANSLATE_MODEL}...\n")
        tr = await translation_test(client, SAMPLE_TEXT, args.target, glossary)
        print_summary([], tr)
        return

    if not args.audio:
        sys.exit("Pasá al menos un audio con --audio (o usá --translate-only).")

    pcms = [load_pcm(p, args.seconds) for p in args.audio]
    stats = [SessionStats(label=f"S{i + 1}") for i in range(len(pcms))]
    print(f"Iniciando {len(stats)} sesión(es) simultánea(s), hasta {args.seconds}s de audio cada una.\n")

    await asyncio.gather(*[
        run_session(client, st, pcm, args.lang, glossary, args.verbose, args.grace)
        for st, pcm in zip(stats, pcms)
    ])

    tr = None
    if not args.no_translate and stats:
        transcript, _ = build_transcript(stats[0])
        if transcript:
            print(f"\nTraduciendo la transcripción de {stats[0].label} a '{args.target}'...\n")
            tr = await translation_test(client, transcript, args.target, glossary)
        else:
            print("\nNo hubo transcripción, se omite la prueba de traducción.")

    print_summary(stats, tr)
    save_events(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description="Spike de validación de Gemini Live + traducción")
    ap.add_argument("--audio", nargs="*", default=[], help="Uno o más archivos de audio (2+ = sesiones simultáneas)")
    ap.add_argument("--lang", default="auto", help="'auto' o un código como en-US, es-ES (por defecto: auto)")
    ap.add_argument("--seconds", type=int, default=45, help="Segundos de audio a usar (por defecto 45)")
    ap.add_argument("--target", default="Spanish", help="Idioma destino de la traducción (por defecto Spanish)")
    ap.add_argument("--glossary", help="Archivo de texto con un término por línea")
    ap.add_argument("--grace", type=float, default=8.0, help="Segundos de espera al final para eventos tardíos")
    ap.add_argument("--verbose", action="store_true", help="Mostrar eventos crudos")
    ap.add_argument("--translate-only", action="store_true", help="Probar solo la traducción")
    ap.add_argument("--no-translate", action="store_true", help="Omitir la prueba de traducción")
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()
