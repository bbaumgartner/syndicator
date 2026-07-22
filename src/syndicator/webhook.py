"""Webhook client for the n8n ``/publish`` and ``/reel`` endpoints.

Both endpoints respond immediately with ``{"status":"accepted"}`` (respond-early
node) and continue async (§4.2). The local client does 3 retries with backoff on
transient failures; a webhook that never returns accepted means the post's
``syndicated-at::`` marker is not written, so the next ``syndicate`` re-runs it.
"""

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
    """POST JSON to a webhook, retrying with backoff. Returns the parsed response.

    Raises ``WebhookError`` when the endpoint does not return an accepted
    response after ``retries`` attempts.
    """
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
                label, attempt, retries, err, wait,
            )
            time.sleep(wait)

    raise WebhookError(f"{label}: not accepted after {retries} attempts") from last_err


def _parse(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {"status": "accepted" if resp.status_code == 200 else "error"}
    return data if isinstance(data, dict) else {"status": "error", "body": data}
