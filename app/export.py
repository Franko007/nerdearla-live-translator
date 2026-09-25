"""Exportación de subtítulos: SRT, VTT y texto plano."""
from __future__ import annotations

from app.models import Segment


def _fmt_ms_srt(ms: int) -> str:
    ms = max(ms, 0)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_ = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_:03d}"


def _fmt_ms_vtt(ms: int) -> str:
    return _fmt_ms_srt(ms).replace(",", ".")


def render_srt(segments: list[Segment], lang: str) -> str:
    out = []
    for i, seg in enumerate(segments, start=1):
        text = _text_for(seg, lang)
        out.append(f"{i}")
        out.append(f"{_fmt_ms_srt(seg.start_ms or 0)} --> {_fmt_ms_srt(seg.end_ms or 0)}")
        out.append(text)
        out.append("")
    return "\n".join(out)


def render_vtt(segments: list[Segment], lang: str) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        out.append(f"{_fmt_ms_vtt(seg.start_ms or 0)} --> {_fmt_ms_vtt(seg.end_ms or 0)}")
        out.append(_text_for(seg, lang))
        out.append("")
    return "\n".join(out)


def render_txt(segments: list[Segment], lang: str) -> str:
    return "\n".join(_text_for(seg, lang) for seg in segments) + "\n"


def _text_for(seg: Segment, lang: str) -> str:
    if lang in seg.translations:
        return seg.translations[lang]
    return seg.source_text