"""Tests for bogi.redaction: secret scrubbing helper + logging filter."""

from __future__ import annotations

import logging

from bogi.redaction import RedactingFilter, redact

# Synthetic, non-real secrets only.
_FAKE_ANTHROPIC = "sk-ant-" + "A" * 30


def test_redact_scrubs_secret() -> None:
    out = redact(f"token {_FAKE_ANTHROPIC} end")
    assert "[REDACTED]" in out
    assert _FAKE_ANTHROPIC not in out


def test_redact_leaves_normal_text_unchanged() -> None:
    normal = "just a normal log line, nothing to see here"
    assert redact(normal) == normal


def test_redact_coerces_non_str() -> None:
    # Must not raise on non-str input.
    assert redact(12345) == "12345"
    assert "[REDACTED]" not in redact({"k": "v"})


def _make_record(msg: object, args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_redacts_msg_and_tuple_args() -> None:
    flt = RedactingFilter()
    record = _make_record("key=%s done", (_FAKE_ANTHROPIC,))
    assert flt.filter(record) is True
    assert isinstance(record.msg, str)
    assert _FAKE_ANTHROPIC not in record.msg  # msg itself had no secret, unchanged
    assert "[REDACTED]" in record.args[0]
    assert _FAKE_ANTHROPIC not in record.args[0]


def test_filter_redacts_msg() -> None:
    flt = RedactingFilter()
    record = _make_record(f"leaked {_FAKE_ANTHROPIC}", None)
    assert flt.filter(record) is True
    assert "[REDACTED]" in record.msg
    assert _FAKE_ANTHROPIC not in record.msg


def test_filter_redacts_dict_args() -> None:
    flt = RedactingFilter()
    # logging stores a sole-mapping arg as record.args = the dict itself.
    record = _make_record("%(token)s", ({"token": _FAKE_ANTHROPIC},))
    assert isinstance(record.args, dict)
    assert flt.filter(record) is True
    assert "[REDACTED]" in record.args["token"]
    assert _FAKE_ANTHROPIC not in record.args["token"]


def test_filter_never_raises_on_weird_input() -> None:
    flt = RedactingFilter()
    # Non-str msg (int) and odd args must not crash the filter.
    assert flt.filter(_make_record(42, None)) is True
    assert flt.filter(_make_record({"a": 1}, None)) is True
    assert flt.filter(_make_record(None, None)) is True
