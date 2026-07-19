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


def test_accepted_response_returns_data():
    with patch("syndicator.webhook.httpx.post", return_value=_resp(200, {"status": "accepted"})):
        data = post_webhook("https://n8n.example/webhook/publish", {"a": 1}, label="/publish")
    assert data == {"status": "accepted"}


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


def test_unexpected_body_is_not_accepted():
    with (
        patch("syndicator.webhook.time.sleep"),
        patch("syndicator.webhook.httpx.post", return_value=_resp(200, {"status": "error"})),
    ):
        with pytest.raises(WebhookError):
            post_webhook("https://n8n.example/webhook/publish", {"a": 1}, retries=2)
