#!/usr/bin/env python3
"""Genera samples de demo para el modo Replay (sin API key).

Crea audios sintéticos con ffmpeg (tonos) y su correspondiente .replay.json
cuya línea de subtítulos es un talk EN -> ES plausible.

Uso:
  python scripts/make_samples.py
  # o via uv:  uv run python scripts/make_samples.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

# (start_ms, end_ms, source_en, translation_es)
TALK_1 = [
    (600, 3600, "Hi everyone and welcome to Nerdearla 2026.", "Hola a todos y bienvenidos a Nerdearla 2026."),
    (3800, 7600, "Today we're going to talk about running Kubernetes in production.",
     "Hoy vamos a hablar de correr Kubernetes en producción."),
    (8200, 11800, "The first question is simple. Do you monitor your nodes?",
     "La primera pregunta es simple. ¿Monitorean sus nodos?"),
    (12400, 16400, "Most teams start with Prometheus and Grafana.",
     "La mayoría de los equipos empieza con Prometheus y Grafana."),
    (17200, 21000, "But observability is more than dashboards.",
     "Pero la observabilidad es más que dashboards."),
    (22400, 27200, "OpenTelemetry gives you traces, metrics and logs from one SDK.",
     "OpenTelemetry te da traces, métricas y logs desde un único SDK."),
    (28000, 32000, "Health checks decide whether your instance gets traffic.",
     "Los health checks deciden si tu instancia recibe tráfico."),
    (33200, 37400, "Ready and live probes have very different jobs.",
     "Las sondas ready y live tienen trabajos muy distintos."),
    (38200, 42400, "And never forget the glossary. Kubernetes keeps its name.",
     "Y nunca olviden el glosario. Kubernetes conserva su nombre."),
    (43600, 48800, "Thanks for watching. See you in the next talk.",
     "Gracias por mirar. Nos vemos en la próxima charla."),
]

TALK_2 = [
    (700, 3500, "Welcome back everybody. Great to see you all here.",
     "Bienvenidos de nuevo. Qué gusto verlos a todos acá."),
    (4000, 7800, "Today I want to talk about WebAssembly outside the browser.",
     "Hoy quiero hablar de WebAssembly fuera del navegador."),
    (8500, 12500, "It runs in edge runtimes, in databases, even in kernels.",
     "Corre en runtimes de edge, en bases de datos, incluso en kernels."),
    (13200, 17000, "The key idea is sandboxing untrusted code safely.",
     "La idea clave es aislar código no confiable de forma segura."),
    (17800, 22400, "You get near-native performance with portable binaries.",
     "Obtienes rendimiento casi nativo con binarios portables."),
    (23200, 27600, "At the module level, you don't compile to a machine.",
     "A nivel de módulo, no compilas a una máquina."),
    (28400, 32000, "You compile once and run anywhere.",
     "Compilas una vez y corre en cualquier lado."),
    (33200, 38000, "Choose it when you need isolation but not a container.",
     "Elegilo cuando necesites aislamiento pero no un contenedor."),
    (39200, 43800, "Thanks a lot. I'll take questions over lunch.",
     "Muchas gracias. Respondo preguntas durante el almuerzo."),
]


def make_wav(name: str, freq: int, seconds: int = 58) -> None:
    path = SAMPLES / f"{name}.wav"
    if path.is_file():
        print(f"Já existe {path}")
        return
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-af", "volume=0.25", "-ar", "16000", "-ac", "1", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"Generado {path}")


def make_replay(name: str, title: str, talk: list[tuple]) -> None:
    segments = []
    events = []
    for seq, (start, end, en, es) in enumerate(talk, start=1):
        segments.append({
            "seq": seq,
            "start_ms": start,
            "end_ms": end,
            "source_text": en,
            "translations": {"es": es},
        })
        # un parcial previo al final, estilo acumulativo (igual que Gemini)
        events.append({"at_ms": start + 900, "kind": "partial", "text": en})
        events.append({"at_ms": end, "kind": "final", "text": en, "start_ms": start, "end_ms": end})


    data = {
        "version": 1,
        "session_id": name,
        "title": title,
        "source_lang": "en",
        "target_langs": ["es"],
        "audio": f"samples/{name}.wav",
        "events": events,
        "segments": segments,
    }
    out = SAMPLES / f"{name}.replay.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generado {out}")


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    make_wav("talk-en-1", 220)
    make_wav("talk-en-2", 330)
    make_replay("talk-en-1", "Kubernetes at scale", TALK_1)
    make_replay("talk-en-2", "WebAssembly beyond the browser", TALK_2)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"ffmpeg falló: {e.stderr.decode(errors='ignore')}")