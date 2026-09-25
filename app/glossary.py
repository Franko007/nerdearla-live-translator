"""Carga del glosario (un término por línea)."""
from __future__ import annotations

from pathlib import Path


def load_glossary(path: Path) -> list[str]:
    if not path or not Path(path).is_file():
        return []
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]