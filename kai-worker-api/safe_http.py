import json
import logging

logger = logging.getLogger(__name__)


def safe_json(r, default=None):
    """Parse response JSON; return default on decode error to prevent uncaught JSONDecodeError."""
    if default is None:
        default = {}
    try:
        return r.json()
    except (ValueError, json.JSONDecodeError):
        logger.warning("safe_json: non-JSON response status=%s url=%s", r.status_code, getattr(r, "url", "?"))
        return default


def json_or_error(response):
    """Parse response JSON, or return a diagnostic error dict with a short body
    preview when the body is non-JSON (e.g. an HTML error page). Used by the
    WordPress routes, which branch on the _error/_status_code/_body_preview keys.
    Lives outside routes/ so the L3 no-bare-.json() guard stays carve-out free."""
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return {
            "_error": "non_json_response",
            "_status_code": response.status_code,
            "_body_preview": response.text[:200],
        }


async def safe_body(request, default=None):
    """Parse an inbound request body as JSON, never raising (L3). Returns default
    ({} if unset) on an empty, malformed, non-JSON, or interrupted body — the
    fail-safe contract the Telegram webhook and scheduler ticks rely on."""
    if default is None:
        default = {}
    try:
        return await request.json()
    except Exception:
        return default
