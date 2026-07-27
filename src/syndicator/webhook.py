"""Webhook client for the n8n ``/publish`` and ``/reel`` endpoints."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class WebhookError(RuntimeError):
    """A webhook could not be delivered/accepted after all retries."""


def post_webhook(
    url: str,
    payload: dict,
    *,
    label: str = "webhook",
    retries: int = 3,
    timeout: float = 60.0,
) -> dict:
    if not url:
        raise WebhookError(f"{label}: no webhook URL configured")

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = _parse(resp)
            if data.get("status") != "accepted":
                raise WebhookError(f"{label}: unexpected response {data!r}")
            log.info("%s accepted (attempt %d)", label, attempt)
            return data
        except (httpx.HTTPError, WebhookError) as err:
            last_err = err
            if attempt == retries:
                break
            wait = 2 ** (attempt - 1)
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %ds",
                label,
                attempt,
                retries,
                err,
                wait,
            )
            time.sleep(wait)

    raise WebhookError(f"{label}: not accepted after {retries} attempts") from last_err


def _parse(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {"status": "accepted" if resp.status_code == 200 else "error"}
    return data if isinstance(data, dict) else {"status": "error", "body": data}
