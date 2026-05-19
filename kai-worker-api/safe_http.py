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
