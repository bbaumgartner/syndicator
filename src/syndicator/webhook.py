"""Webhook client for the n8n ``/publish`` and ``/reel`` endpoints."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class WebhookError(RuntimeError):
    """A webhook could not be delivered after all retries."""


def post_webhook(
    url: str,
    payload: dict,
    *,
    label: str = "webhook",
    retries: int = 3,
    timeout: float = 60.0,
) -> None:
    """POST ``payload`` and treat any HTTP 2xx as a successful hand-off.

    n8n webhooks use ``responseMode: onReceived`` so the HTTP response is sent
    as soon as the request is accepted (even when execution is queued under
    ``N8N_CONCURRENCY_PRODUCTION_LIMIT=1``). Body content is ignored.
    """
    if not url:
        raise WebhookError(f"{label}: no webhook URL configured")

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            log.info("%s accepted HTTP %s (attempt %d)", label, resp.status_code, attempt)
            return
        except httpx.HTTPError as err:
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
