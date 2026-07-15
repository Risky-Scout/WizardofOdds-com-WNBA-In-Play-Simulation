from __future__ import annotations

import json
from typing import Any, Mapping

from .validation import validate_report_dict


class ReferenceAPI:
    """Dependency-free HTTP contract handler.

    A production deployment can wrap this handler with ASGI, WSGI, or a
    framework adapter while preserving the tested request/response semantics.
    """

    def handle(
        self,
        method: str,
        path: str,
        body: bytes = b"",
    ) -> tuple[int, dict[str, str], bytes]:
        try:
            if method == "GET" and path == "/health":
                return self._json(200, {"status": "ok"})

            if method == "GET" and path == "/v1/capabilities":
                return self._json(
                    200,
                    {
                        "layers": [
                            "event_replay",
                            "rotation",
                            "simulation",
                            "pricing_risk",
                            "artifact_validation",
                        ],
                        "public_edge_field": "time_decay_adjusted_edge",
                    },
                )

            if method == "POST" and path == "/v1/validate-report":
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    return self._json(
                        400,
                        {"status": "error", "reason": "object required"},
                    )
                result = validate_report_dict(payload)
                return self._json(
                    200 if result.valid else 422,
                    {
                        "valid": result.valid,
                        "reasons": list(result.reasons),
                        "duplicate_pmfs": result.duplicate_pmfs,
                        "invalid_pmfs": result.invalid_pmfs,
                        "duplicate_market_rows":
                            result.duplicate_market_rows,
                        "stale_artifacts": result.stale_artifacts,
                    },
                )

            return self._json(
                404,
                {"status": "error", "reason": "not found"},
            )
        except json.JSONDecodeError:
            return self._json(
                400,
                {"status": "error", "reason": "invalid JSON"},
            )

    @staticmethod
    def _json(
        status: int,
        payload: Mapping[str, Any],
    ) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return (
            status,
            {
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
            body,
        )
