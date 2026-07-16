from __future__ import annotations

import logging
import re


# Redact secret material from any log line. Covers the two provider query-string
# secrets (apiKey / api_key), bearer/authorization headers, and generic token=
# parameters, whether they appear in a URL, a dict repr, or a header dump.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(apiKey=)[^&\s\"'}]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(api_key[\"']?\s*[:=]\s*[\"']?)[^&\s\"'},]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+)?[^\s,\"'}]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(token=)[^&\s\"'}]+", re.IGNORECASE), r"\1<REDACTED>"),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """A logging filter that scrubs secrets from formatted log output.

    It redacts the *fully formatted* message (message % args), so secrets are
    caught even when they arrive as non-string args — e.g. httpx logs the
    request URL (which carries ``apiKey=``) as an ``httpx.URL`` object, not a
    ``str``. Formatting first, then folding the result back into ``msg`` with
    empty args, guarantees the emitted line is scrubbed regardless of arg type.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatted = record.getMessage()
            redacted = redact(formatted)
            if redacted != formatted:
                record.msg = redacted
                record.args = ()
        except Exception:  # pragma: no cover - never break logging
            pass
        return True


_INSTALLED = False


def install_log_redaction() -> None:
    """Attach the redacting filter to the root handlers and provider loggers.

    Idempotent: safe to call from both the worker entrypoint and the API
    startup without stacking duplicate filters.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    log_filter = RedactingFilter()

    root = logging.getLogger()
    root.addFilter(log_filter)
    for handler in root.handlers:
        handler.addFilter(log_filter)

    # httpx/httpcore log outbound request URLs (which carry apiKey); uvicorn
    # access logs inbound paths. Filter each directly so a record logged there
    # is scrubbed even if it does not reach a root handler.
    for name in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(log_filter)

    _INSTALLED = True
