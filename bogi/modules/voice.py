"""Voice transcription.

Telegram voice messages are OGG/Opus.

Two backends, selected at call time by whether an OpenAI key is configured:

* **OpenAI Whisper** (`whisper-1`) — used only when `OPENAI_API_KEY` is set.
  Faster, but requires a paid key. Bypasses the LiteLLM proxy because audio
  transcription isn't in `litellm/config.yaml` and Pydantic-AI's OpenAI client
  doesn't speak the audio API.
* **Local faster-whisper** — the key-free default. Runs a quantized Whisper
  model on CPU via a module-level singleton. Decodes ogg/opus through the
  PyAV bundled with faster-whisper, so no ffmpeg/system deps are needed.

Public signature is stable: ``transcribe(audio_bytes, filename="voice.ogg")``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from bogi.config import settings

logger = logging.getLogger(__name__)

# Defaults are tuned for key-free CPU use. Override the model size via VOICE_MODEL
# (e.g. "tiny"/"base"/"small"/"medium"). int8 on CPU keeps memory + latency sane.
_DEFAULT_MODEL = "small"
_COMPUTE_TYPE = "int8"
_DEVICE = "cpu"

# Lazily-loaded faster-whisper singleton — built once on first local transcription.
_local_model = None


def _make_client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is empty — cannot transcribe audio")
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _get_local_model():
    """Build (once) and return the faster-whisper model singleton.

    Imported lazily so that merely importing this module never requires
    faster-whisper to be installed or a model to be downloaded.
    """
    global _local_model
    if _local_model is not None:
        return _local_model

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise RuntimeError(
            "faster-whisper is not installed — run "
            "`.venv/Scripts/python -m pip install faster-whisper` "
            "or set OPENAI_API_KEY to use the OpenAI backend"
        ) from exc

    model_size = os.environ.get("VOICE_MODEL", _DEFAULT_MODEL)
    logger.info("Loading faster-whisper model %r (device=%s, compute=%s)", model_size, _DEVICE, _COMPUTE_TYPE)
    _local_model = WhisperModel(model_size, device=_DEVICE, compute_type=_COMPUTE_TYPE)
    return _local_model


async def _transcribe_openai(audio_bytes: bytes, filename: str) -> str:
    client = _make_client()
    # Whisper API wants a file-like object with a .name attribute for format detection.
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    try:
        resp = await client.audio.transcriptions.create(
            model="whisper-1",
            file=buf,
            response_format="text",
        )
    except Exception:
        logger.exception("Whisper transcription failed")
        raise
    text = resp if isinstance(resp, str) else getattr(resp, "text", "")
    return (text or "").strip()


def _run_local(path: str) -> str:
    """Synchronous, CPU-heavy transcription. Runs inside asyncio.to_thread."""
    model = _get_local_model()
    segments, _info = model.transcribe(path, language="bg")
    return "".join(segment.text for segment in segments).strip()


async def _transcribe_local(audio_bytes: bytes, filename: str) -> str:
    suffix = Path(filename).suffix or ".ogg"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.flush()
        tmp.close()
        try:
            return await asyncio.to_thread(_run_local, tmp.name)
        except Exception:
            logger.exception("Local faster-whisper transcription failed")
            return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            logger.warning("Could not remove temp audio file %s", tmp.name)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe an audio blob. Returns plain text (empty string on failure).

    Uses OpenAI Whisper when `OPENAI_API_KEY` is set, otherwise the local,
    key-free faster-whisper backend.
    """
    if settings.openai_api_key:
        return await _transcribe_openai(audio_bytes, filename)
    return await _transcribe_local(audio_bytes, filename)
