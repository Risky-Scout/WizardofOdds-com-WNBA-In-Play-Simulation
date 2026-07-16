from __future__ import annotations

import logging

from wizard_wnba.logging_redaction import RedactingFilter, redact


class _UrlLike:
    """Mimics httpx.URL: a non-str object whose str() carries the secret."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __str__(self) -> str:
        return self._url


def _emit(msg, args) -> str:
    record = logging.LogRecord(
        name="httpx", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )
    RedactingFilter().filter(record)
    return record.getMessage()


def test_redacts_apikey_in_plain_string():
    out = redact("GET /v4/odds?apiKey=SUPERSECRET123&regions=us")
    assert "SUPERSECRET123" not in out
    assert "<REDACTED>" in out


def test_redacts_bearer_and_authorization():
    assert "abc.def" not in redact("authorization: Bearer abc.def.ghi")
    assert "abc.def" not in redact("Authorization=Bearer abc.def")


def test_redacts_url_object_arg_like_httpx_access_log():
    # Reproduces httpx: logger.info('HTTP Request: %s %s "%s"', method, url, status)
    url = _UrlLike("https://api.the-odds-api.com/v4/odds?apiKey=LIVEKEYABCDEF123&x=1")
    out = _emit('HTTP Request: %s %s "%s"', ("GET", url, "HTTP/1.1 200 OK"))
    assert "LIVEKEYABCDEF123" not in out
    assert "<REDACTED>" in out
    # The rest of the line survives.
    assert "HTTP Request: GET" in out
    assert "200 OK" in out


def test_non_secret_line_unchanged():
    out = _emit("processed %d games", (4,))
    assert out == "processed 4 games"
