# nerdearla-live-translator

Captioning y traducción simultánea open source para eventos (Nerdearla).
El audio entra por una **URL (HLS/archivo/YouTube)** o directo por el
**micrófono del navegador** (placa de consola → jack 3.5 → mini-PC), se
transcribe con **Gemini 3.5 Transcribe Live** y se traduce en tiempo real a
múltiples idiomas. Los subtítulos salen por **SSE** (overlay para
pantallas/OBS/vMix) y se pueden exportar como SRT/VTT/TXT.

## Cómo funciona

```
Fuente de audio (HLS Castr · sample · YouTube · mic del navegador)
   │
   ├─ URL/archivo → ffmpeg → chunks PCM 16kHz
   └─ Mic browser → WebSocket /ws/audio/{id} → chunks PCM 16kHz
   ▼
GeminiTranscriber (streaming bi-direccional, reconnect automático)
   │  partials + finals (en-US)
   ▼
Segmenter (apertura/cierre de segmentos por silencio y ventana)
   │
   ├─ Final  ──► Translator (Gemini flash-lite) → traducción en vivo (SSE)
   │
   └─ Eventos wire (seq + timestamp) → Hub (buffer histórico)
                                    │
         SSE /stream/{id}?lang=es   │   /export/{id}.srt|vtt|txt
         overlay OBS/vMix /overlay/{id}  ◄┘   /api/sessions
```

- **Modo DEMO**: `PROVIDER=replay` — reproduce `samples/*.wav` + `*.replay.json`
  por el mismo pipeline, sin API ni credenciales. Sirve de demo y de que no se
  rompe nada sin billing/cuenta. **Solo reproduce archivos locales**: URLs,
  YouTube y HLS requieren modo LIVE (`PROVIDER=gemini`).
- **Modo LIVE**: `PROVIDER=gemini` — Vertex AI (recomendado) o AI Studio.

## Requisitos

- Python >= 3.11 + [uv](https://docs.astral.sh/uv/) (o venv clásico)
- `ffmpeg` en PATH
- Docker (para el deploy)
- Solo para LIVE: credenciales de Gemini (ver "Modos de IA" más abajo)

## Correr en local

```bash
uv sync --dev
uv run uvicorn app.main:app --port 8080        # Demo: samples + traducciones
PROVIDER=gemini uv run uvicorn app.main:app    # Live (si hay credenciales)
```

Abrir http://localhost:8080 (audiencia), `/admin` (panel) y
`/overlay/<session_id>?lang=es` para el Browser Source de OBS/vMix.

### Capturar el audio del mic (navegador → WebSocket)

El flujo pensado para el evento: la placa de la consola entra por jack 3.5 a la
mini-PC y el navegador la captura directo. En `/admin`:

1. Elegís **idioma origen/destino** (o el mismo origen = solo transcripción,
   sin llamadas de traducción).
2. Click en **🎙 Capturar micrófono** → pedís permiso de mic, se crea la
   sesión en un paso y el audio viaja por `WS /ws/audio/{id}` en PCM 16 kHz.
3. La sesión **se corta sola** a los instantes de caerse el audio (o con el
   botón **Stop**). El indicador de nivel (VU) te confirma que está entrando.

```bash
# El server puede correr en la misma mini-PC (LAN):
PROVIDER=gemini GEMINI_API_KEY="$KEY" uv run uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Cualquier WebSocket puede alimentar una sesión (mismo protocolo que el
navegador): mandar bytes PCM s16le 16 kHz mono por `ws://host/ws/audio/{id}` y
cerrar el socket cuando termina. La captura queda a un solo capturador por
sesión (intentos extra se rechazan con `1008`).

## Tests

```bash
uv run pytest -q
```

`tests/test_integration.py` levanta la app completa en un uvicorn real y valida
SSE (buffer + Last-Event-ID), exportaciones, la API (POST/crear, delete) y la
ingesta por **WebSocket** (creación de sesión, rechazo de doble captura,
auto-stop y caso es→es sin traducción).

## Endpoints

| Ruta | Descripción |
|---|---|
| `/` | Frontend de audiencia |
| `/admin` | Panel de operación |
| `/stream/{id}?lang=es` | SSE de subtítulos (soporta `Last-Event-ID`) |
| `/overlay/{id}?lang=es` | Overlay para OBS/vMix Browser Source (fondo transparente) |
| `/ws/audio/{id}` | Captura del mic del navegador (PCM 16 kHz binario) |
| `/export/{id}.srt` / `.vtt` / `.txt` | Exportación de la transcripción |
| `/api/sessions` | GET listar / POST crear sesión |
| `/api/resolve-source` | POST: resuelve URL de YouTube con yt-dlp |
| `/api/sessions/{id}` | DELETE: detener y eliminar sesión; POST `/stop` |
| `/healthz` | Health check |

## Modos de IA (LIVE)

La app soporta dos backends para Gemini, elegibles con `GOOGLE_GENAI_USE_VERTEXAI`:

| | Vertex AI (recomendado) | AI Studio (API key) |
|---|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` | `false` |
| Credencial | ADC (`gcloud auth application-default login`) | `GEMINI_API_KEY` |
| Transcribe | `publishers/google/models/gemini-3.5-transcribe-live-preview` | `models/gemini-3.5-transcribe-live` |
| Location | `GOOGLE_CLOUD_LOCATION` (`global` o `us-central1`) | — |

Módulos relevantes: `app/transcribers/gemini.py` (streaming), el client se crea
en `build_gemini_client` y el modelo con `backend_model(settings, ...)`.

### Notas sobre Vertex AI

- El transcriber usa `LiveConnectConfig` con `response_modalities=["TEXT"]` y
  `input_audio_transcription` para `en-US`. El flag para silencios lo maneja el
  Segmenter; el stream reconecta con backoff automático (1→8 s).
- El proyecto de este MVP hace falta la generación 3.x en `publishers/google`,
  por eso la traducción usa `gemini-2.5-flash` por defecto en Vertex
  (`_translate_model` en `app/providers.py`).
- **Billing**: la clave pasa por que el proyecto tenga una billing account
  abierta. Si `gcloud billing projects describe <PROJECT>` da
  `billingEnabled: false`, los preview de live AI devuelven
  `This API method requires billing to be enabled` (código 1008).

### Muleta sin billing

`GeminiTranscriber` acepta `chunked=True` (constantes `CHUNK_MS`/`CHUNK_PROMPT`
en `app/transcribers/gemini.py`): parte el audio en ventanas (p. ej. 4 s) y
transcribe cada una con `generate_content` de flash-lite. Es notablemente más
latente que el streaming pero **funciona sin billing y sin cuenta de GCP**,
solo con `GEMINI_API_KEY`.

```bash
# Demo AI Studio por chunks (sin billing, solo API key)
GEMINI_API_KEY="$KEY" PROVIDER=gemini GOOGLE_GENAI_USE_VERTEXAI=false \
  uv run python scripts/run_live_local.py --audio samples/speech-tts-en.wav --chunked

# Mismo camino con una URL (p. ej. un live de YouTube resuelto por /api/resolve-source)
GEMINI_API_KEY="$KEY" PROVIDER=gemini GOOGLE_GENAI_USE_VERTEXAI=false \
  uv run python scripts/run_live_local.py --audio "https://....m3u8" --chunked
```

## Subtítulos en vMix / OBS

El mismo overlay cubre pantallas del escenario, OBS y vMix.

| Dónde | Cómo |
|---|---|
| **vMix** | Input → **Add Input** → **Web Browser** → URL `http://mini-pc:8080/overlay/ID?lang=es` (fondo 100 % transparente, tamaño fullscreen) |
| **OBS** | Fuente → **Navegador** → misma URL del overlay |
| **Pantallas del escenario** | Pestaña del browser apuntando al overlay, o la vista `/` para el público |
| **Celular** | QR en el admin (`◇ QR`) apunta a `/`; se escanea estando en la misma red |

El overlay muestra original arriba y traducción abajo; para "planchar" subtítulos
en el live stream no hace falta nada más del lado de vMix. Si preferís títulos
nativos, el archivo `SRT` se exporta por `/export/{id}.srt?lang=es`.

> Latencia esperada: transcripción ~1-2 s y traducción ~1-3 s más. En el stream
> esto se compensa solo (el live de vMix ya lleva su propio delay), no hay que
> sincronizar a mano.

## Docker (demo/local)

```bash
docker compose up -d --build          # PROVIDER=replay por la compose
# http://localhost:8080
```

## Deploy a Cloud Run

Requisitos previos (los hace quien levanta el MVP): una cuenta de **billing
abierta** en el proyecto `safeapp-b32af`; con eso el propio script habilita las
APIs necesarias (run + artifactregistry) si faltan.

```bash
gcloud auth login
gcloud config set project safeapp-b32af
gcloud auth application-default login

# Demo (samples, sin Gemini)
PROVIDER=replay ./scripts/deploy_gcloud.sh

# Live con HLS de Castr (Vertex AI)
NERDEARLA_STREAM_URL="https://.../playlist.m3u8" \
PROVIDER=gemini TARGET_LANGS="es,en" ./scripts/deploy_gcloud.sh
```

El script termina imprimiendo la URL y las rutas de health/sessions/overlay.

Si el billing estuviera caído, simplemente:
1. Consola GCP → **Billing** → crear/vincular una billing account al proyecto,
2. Re-correr `./scripts/deploy_gcloud.sh` (habilita APIs y deploy sola).

> Nota: `run.googleapis.com` no puede habilitarse sin billing; el script lo
> detecta y aborta con instrucciones. El resto del MVP (modo DEMO local y
> Docker) no depende del billing.

## Configuración (variables de entorno)

| Variable | Defecto | Descripción |
|---|---|---|
| `PROVIDER` | `replay` | `gemini` (live) o `replay` (demo) |
| `GEMINI_API_KEY` | — | Clave AI Studio (solo `GOOGLE_GENAI_USE_VERTEXAI=false`) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false` | `true` para Vertex AI en GCP |
| `GOOGLE_CLOUD_PROJECT` | — | Proyecto GCP |
| `GOOGLE_CLOUD_LOCATION` | `global` | `global` o `us-central1` |
| `TRANSCRIBE_MODEL` | `gemini-3.5-transcribe-live` | Modelo de transcripción |
| `TRANSLATE_MODEL` | `gemini-3.5-flash-lite` | Modelo de traducción |
| `NERDEARLA_STREAM_URL` | — | HLS `.m3u8` a transcribir (en modo live) |
| `SOURCE_LANG_DEFAULT` | `en` | Idioma del audio |
| `TARGET_LANGS` | `es,en` | Idiomas a los que traducir (CSV) |
| `GLOSSARY_PATH` | `glossary.txt` | Términos a respetar en traducción |
| `RECORD` | `false` | Graba una corrida de Gemini como `.replay.json` |
| `SAMPLES_DIR` | `samples` | Dir de samples del modo replay |

## Grabaciones de replay

`RECORD=1` en modo live guarda la corrida real de Gemini como `*.wav` +
`*.replay.json` (que se reproducen luego en modo `replay` por el mismo
pipeline). `scripts/make_samples.py` genera demos sintéticas a mano (tonos +
texto en los `.replay.json`, sin API). El formato queda documentado en
`app/replay.py`.

## Licencia

Apache-2.0.