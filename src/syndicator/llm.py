"""OpenAI wrapper: per-node model selection, retries, structured outputs.

Tests inject a fake client with the same interface instead of this class
(see tests/conftest.py), so production code carries no test logic.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


class LLMClient:
    """Thin wrapper around the OpenAI API."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def _with_retries(self, call):
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return call()
            except Exception as err:  # noqa: BLE001 - retry then re-raise
                last_err = err
                wait = 2**attempt
                log.warning("LLM call failed (attempt %d/%d): %s — retrying in %ds",
                            attempt + 1, self.max_retries, err, wait)
                time.sleep(wait)
        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts") from last_err

    @staticmethod
    def _is_temperature_error(err: Exception) -> bool:
        return "temperature" in str(err).lower()

    def complete_text(
        self,
        node: str,
        model: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        def call(temp=temperature):
            try:
                completion = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **({"temperature": temp} if temp is not None else {}),
                )
            except Exception as err:
                # Some models reject non-default temperatures; retry without.
                if temp is not None and self._is_temperature_error(err):
                    return call(temp=None)
                raise
            return completion.choices[0].message.content or ""

        return self._with_retries(call)

    def complete_structured(
        self,
        node: str,
        model: str,
        system: str,
        user_content: str | list,
        schema: type[T],
        temperature: float | None = None,
    ) -> T:
        def call(temp=temperature):
            try:
                completion = self.client.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    response_format=schema,
                    **({"temperature": temp} if temp is not None else {}),
                )
            except Exception as err:
                if temp is not None and self._is_temperature_error(err):
                    return call(temp=None)
                raise
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(f"model returned no parseable {schema.__name__}")
            return parsed

        return self._with_retries(call)
