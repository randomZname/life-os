"""Voice transcription tests — no network, no model download.

Both backends are faked:

* the local faster-whisper path is exercised by monkeypatching
  ``voice._get_local_model`` to return a fake model (so no real WhisperModel is
  built and nothing is downloaded);
* the OpenAI path is exercised by monkeypatching ``voice._make_client`` to
  return a fake async client.

Backend selection keys off ``settings.openai_api_key``, which we set per test.
"""

from __future__ import annotations

import os

from bogi.config import settings
from bogi.modules import voice

# --- fakes -------------------------------------------------------------------


class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeLocalModel:
    """Mimics faster-whisper's WhisperModel.transcribe → (segments, info)."""

    def __init__(self, segments, *, raises: Exception | None = None):
        self._segments = segments
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self._raises is not None:
            raise self._raises
        return (iter(self._segments), {"language": "bg"})


class _FakeTranscriptions:
    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class _FakeAudio:
    def __init__(self, transcriptions):
        self.transcriptions = transcriptions


class _FakeOpenAIClient:
    def __init__(self, result):
        self.audio = _FakeAudio(_FakeTranscriptions(result))


# --- helpers -----------------------------------------------------------------


def _no_openai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")


def _with_openai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")


# --- import safety -----------------------------------------------------------


def test_import_requires_no_key_or_model():
    # Importing the module must not build a model or need a key.
    assert voice._local_model is None
    assert hasattr(voice, "transcribe")


# --- local backend -----------------------------------------------------------


async def test_local_path_joins_segments(monkeypatch):
    _no_openai(monkeypatch)
    fake = _FakeLocalModel([_FakeSegment("Здравей "), _FakeSegment("свят")])
    monkeypatch.setattr(voice, "_get_local_model", lambda: fake)

    out = await voice.transcribe(b"fake-ogg-bytes", filename="voice.ogg")

    assert out == "Здравей свят"
    # transcribe called once, with Bulgarian language hint
    assert len(fake.calls) == 1
    _path, kwargs = fake.calls[0]
    assert kwargs.get("language") == "bg"


async def test_local_path_cleans_up_temp_file(monkeypatch):
    _no_openai(monkeypatch)
    seen_paths: list[str] = []

    fake = _FakeLocalModel([_FakeSegment("ok")])

    def _capture():
        return fake

    monkeypatch.setattr(voice, "_get_local_model", _capture)

    # Wrap _run_local to capture the temp path the model was given.
    orig_run = voice._run_local

    def _wrapped(path):
        seen_paths.append(path)
        return orig_run(path)

    monkeypatch.setattr(voice, "_run_local", _wrapped)

    await voice.transcribe(b"bytes", filename="clip.ogg")

    assert seen_paths, "temp file path was never produced"
    tmp_path = seen_paths[0]
    assert tmp_path.endswith(".ogg")
    assert not os.path.exists(tmp_path), "temp file was not cleaned up"


async def test_local_garbage_audio_returns_empty(monkeypatch):
    _no_openai(monkeypatch)
    fake = _FakeLocalModel([], raises=RuntimeError("could not decode audio"))
    monkeypatch.setattr(voice, "_get_local_model", lambda: fake)

    out = await voice.transcribe(b"not-audio", filename="garbage.ogg")
    assert out == ""


async def test_local_empty_segments_returns_empty(monkeypatch):
    _no_openai(monkeypatch)
    fake = _FakeLocalModel([])
    monkeypatch.setattr(voice, "_get_local_model", lambda: fake)

    out = await voice.transcribe(b"silence", filename="silent.ogg")
    assert out == ""


# --- OpenAI backend ----------------------------------------------------------


async def test_openai_path_used_when_key_set(monkeypatch):
    _with_openai(monkeypatch)
    client = _FakeOpenAIClient("Транскрипция от OpenAI")
    monkeypatch.setattr(voice, "_make_client", lambda: client)

    # If the local model were used this would blow up — make sure it isn't.
    def _boom():
        raise AssertionError("local model must not be used when OPENAI_API_KEY is set")

    monkeypatch.setattr(voice, "_get_local_model", _boom)

    out = await voice.transcribe(b"audio", filename="voice.ogg")

    assert out == "Транскрипция от OpenAI"
    assert client.audio.transcriptions.calls, "OpenAI client was not called"
    assert client.audio.transcriptions.calls[0]["model"] == "whisper-1"
