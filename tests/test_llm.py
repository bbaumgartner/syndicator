"""Tests for the LLM wrapper: retries, temperature fallback, structured parsing."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from syndicator.llm import LLMClient, image_data_url


class _Schema(BaseModel):
    value: str = ""


def _completion(content=None, parsed=None):
    message = SimpleNamespace(content=content, parsed=parsed)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeCompletions:
    """Scriptable chat.completions endpoint: pop one behavior per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _next(self, kwargs):
        self.calls.append(kwargs)
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def create(self, **kwargs):
        return self._next(kwargs)

    def parse(self, **kwargs):
        return self._next(kwargs)


def _client(script) -> tuple[LLMClient, _FakeCompletions]:
    llm = LLMClient(max_retries=3)
    completions = _FakeCompletions(script)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


def test_complete_text_retries_then_succeeds():
    llm, completions = _client([RuntimeError("boom"), _completion(content="ok")])
    with patch("syndicator.llm.time.sleep") as sleep:
        assert llm.complete_text("n", "m", "sys", "user") == "ok"
    assert len(completions.calls) == 2
    sleep.assert_called_once_with(1)


def test_complete_text_raises_after_max_retries_without_final_sleep():
    llm, completions = _client([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
    with patch("syndicator.llm.time.sleep") as sleep:
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            llm.complete_text("n", "m", "sys", "user")
    assert len(completions.calls) == 3
    # No pointless sleep after the last attempt.
    assert sleep.call_count == 2


def test_complete_text_drops_temperature_when_model_rejects_it():
    llm, completions = _client(
        [RuntimeError("Unsupported value: 'temperature' does not support 0.3"),
         _completion(content="ok")]
    )
    with patch("syndicator.llm.time.sleep"):
        assert llm.complete_text("n", "m", "sys", "user", temperature=0.3) == "ok"
    assert "temperature" in completions.calls[0]
    assert "temperature" not in completions.calls[1]


def test_complete_text_returns_empty_string_for_none_content():
    llm, _ = _client([_completion(content=None)])
    assert llm.complete_text("n", "m", "sys", "user") == ""


def test_complete_structured_returns_parsed_and_retries_on_none():
    llm, completions = _client(
        [_completion(parsed=None), _completion(parsed=_Schema(value="x"))]
    )
    with patch("syndicator.llm.time.sleep"):
        result = llm.complete_structured("n", "m", "sys", "user", _Schema)
    assert result.value == "x"
    assert len(completions.calls) == 2


def test_complete_structured_drops_temperature_when_model_rejects_it():
    llm, completions = _client(
        [RuntimeError("temperature not supported"), _completion(parsed=_Schema(value="y"))]
    )
    with patch("syndicator.llm.time.sleep"):
        result = llm.complete_structured("n", "m", "sys", "user", _Schema, temperature=0.9)
    assert result.value == "y"
    assert "temperature" not in completions.calls[1]


def test_image_data_url(tmp_path: Path):
    png = tmp_path / "img.png"
    png.write_bytes(b"\x89PNG\r\n")
    url = image_data_url(png)
    assert url.startswith("data:image/png;base64,")

    unknown = tmp_path / "img.raw3"
    unknown.write_bytes(b"x")
    assert image_data_url(unknown).startswith("data:image/jpeg;base64,")
