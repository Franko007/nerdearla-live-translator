"""Configuración global del proyecto vía variables de entorno."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("target_langs", mode="before")
    @classmethod
    def _split_langs(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    provider: str = "replay"          # "gemini" | "replay"
    record: bool = False              # grabar corrida real de Gemini a .replay.json

    # Gemini / Vertex AI
    gemini_api_key: str = ""
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    transcribe_model: str = "gemini-3.5-transcribe-live"
    translate_model: str = "gemini-3.5-flash-lite"

    # Demostración en vivo: HLS de Castr (Nerdearla)
    nerdearla_stream_url: str = ""

    # Idioma
    source_lang_default: str = "en"
    target_langs: Annotated[list[str], NoDecode] = ["es", "en"]

    # Glosario
    glossary_path: Path = Path("glossary.txt")

    # Límites / runtime
    max_sessions: int = 10
    port: int = 8080

    # Replay: directorio de samples
    samples_dir: Path = Path("samples")

    @property
    def is_replay(self) -> bool:
        return self.provider.lower() == "replay"


@lru_cache
def get_settings() -> Settings:
    return Settings()