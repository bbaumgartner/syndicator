"""Tests for the webhook client (accept handling + retries)."""

from unittest.mock import patch

import httpx
import pytest

from syndicator.webhook import WebhookError, post_webhook


def _resp(status_code=200, json_body=None):
    req = httpx.Request("POST", "https://n8n.example/webhook/publish")
    if json_body is None:
        return httpx.Response(status_code, request=req)
    return httpx.Response(status_code, json=json_body, request=req)


def test_http_200_is_accepted():
    with patch("syndicator.webhook.httpx.post", return_value=_resp(200)):
        post_webhook("https://n8n.example/webhook/publish", {"a": 1}, label="/publish")


def test_http_200_ignores_body():
    """n8n onReceived may return a default body; only the status code matters."""
    with patch(
        "syndicator.webhook.httpx.post",
        return_value=_resp(200, {"message": "Workflow was started"}),
    ):
        post_webhook("https://n8n.example/webhook/publish", {"a": 1})


def test_missing_url_raises():
    with pytest.raises(WebhookError):
        post_webhook("", {"a": 1})


def test_retries_then_raises_on_persistent_failure():
    with (
        patch("syndicator.webhook.time.sleep"),
        patch("syndicator.webhook.httpx.post", side_effect=httpx.ConnectError("down")) as post,
    ):
        with pytest.raises(WebhookError):
            post_webhook("https://n8n.example/webhook/publish", {"a": 1}, retries=3)
    assert post.call_count == 3


def test_http_error_is_retried():
    with (
        patch("syndicator.webhook.time.sleep"),
        patch("syndicator.webhook.httpx.post", return_value=_resp(500)),
    ):
        with pytest.raises(WebhookError):
            post_webhook("https://n8n.example/webhook/publish", {"a": 1}, retries=2)
