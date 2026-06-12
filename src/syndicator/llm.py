"""OpenAI wrapper: per-node model selection, retries, structured outputs,
token/cost ledger and a dry-run mode that never calls the network.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .config import ModelPrice

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class LedgerEntry:
    node: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None


@dataclass
class CostLedger:
    prices: dict[str, ModelPrice] = field(default_factory=dict)
    entries: list[LedgerEntry] = field(default_factory=list)

    def record(self, node: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        price = self.prices.get(model)
        cost = None
        if price is not None:
            cost = (prompt_tokens * price.input + completion_tokens * price.output) / 1_000_000
        self.entries.append(LedgerEntry(node, model, prompt_tokens, completion_tokens, cost))

    @property
    def total_cost_usd(self) -> float:
        return sum(e.cost_usd or 0.0 for e in self.entries)

    def has_unknown_prices(self) -> bool:
        return any(e.cost_usd is None for e in self.entries)

    def summary(self) -> str:
        if not self.entries:
            return "LLM usage: none"
        lines = ["LLM usage:"]
        by_key: dict[tuple[str, str], list[LedgerEntry]] = {}
        for e in self.entries:
            by_key.setdefault((e.node, e.model), []).append(e)
        for (node, model), entries in sorted(by_key.items()):
            pt = sum(e.prompt_tokens for e in entries)
            ct = sum(e.completion_tokens for e in entries)
            cost = sum(e.cost_usd or 0.0 for e in entries)
            known = all(e.cost_usd is not None for e in entries)
            cost_str = f"${cost:.4f}" if known else "n/a"
            lines.append(f"  {node:18s} {model:16s} calls={len(entries):3d} in={pt:7d} out={ct:7d} {cost_str}")
        suffix = " (+ unknown prices)" if self.has_unknown_prices() else ""
        lines.append(f"  total: ${self.total_cost_usd:.4f}{suffix}")
        return "\n".join(lines)


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


class LLMClient:
    """Thin wrapper around the OpenAI API.

    ``dry_run=True`` returns canned outputs and records nothing, so the whole
    pipeline can run without network access or API costs.
    """

    def __init__(self, ledger: CostLedger | None = None, dry_run: bool = False, max_retries: int = 3):
        self.ledger = ledger or CostLedger()
        self.dry_run = dry_run
        self.max_retries = max_retries
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def _record(self, node: str, model: str, completion) -> None:
        usage = getattr(completion, "usage", None)
        if usage is not None:
            self.ledger.record(node, model, usage.prompt_tokens or 0, usage.completion_tokens or 0)

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
        if self.dry_run:
            return f"[dry-run:{node}]"

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
            self._record(node, model, completion)
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
        dry_run_result: T | None = None,
    ) -> T:
        if self.dry_run:
            if dry_run_result is not None:
                return dry_run_result
            return schema.model_construct()

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
            self._record(node, model, completion)
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(f"model returned no parseable {schema.__name__}")
            return parsed

        return self._with_retries(call)
