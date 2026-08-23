"""Secret redaction for persisted + served job/step data (443fb11e, L18).

Root cause it closes: wordpress.load_config returned the site's WP app-password
inside its CapabilityResult.data; the engine persisted that dict verbatim into
the jobs DB `steps.result` column and re-served it in cleartext via GET
/jobs/{id} — so reading a job leaked live credentials into the caller (and into
a Claude session .jsonl, same class as the Syncthing incident d610339f).

Two independent guarantees live here:
  1. redact() — a non-mutating deep-copy scrub used at the PERSIST chokepoint
     (engine._transition_step) so no capability, present or future, can write a
     secret into the jobs DB even if its author forgets.
  2. redact_json_str() — scrubs a JSON-encoded DB string column on the SERVE
     path (GET /jobs, /jobs/{id}) so any legacy row written before this fix
     cannot leak on read either.

Creds still flow between steps in-memory (the workflow re-reads them from the
secrets layer in _ctx) — they simply never touch persistent storage.
"""
import copy
import json
import re

# camelCase / PascalCase → snake_case, so appPassword / accessToken / apiKey
# normalize to the same form as the snake_case keys we match on.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Exact key names whose VALUE is a secret and must never be persisted/served.
SENSITIVE_KEYS = frozenset({
    "app_password", "app-password", "password", "passwd", "pwd",
    "secret", "api_key", "apikey", "authorization", "auth_token",
    "access_token", "refresh_token", "token", "private_key",
    "creds", "credentials",
})

# Any key ENDING in one of these is also treated as a secret (e.g.
# "wp_app_password", "cloudways_api_secret"). Deliberately narrow so benign
# identifiers like "_id" / "_key" as a bare suffix are NOT caught.
SENSITIVE_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_apikey")

_REDACTED = "[REDACTED]"


def _is_sensitive(key) -> bool:
    # Normalize camelCase/PascalCase (appPassword) and kebab/space to snake_case
    # so all spellings collapse onto the same matched form.
    k = _CAMEL.sub("_", str(key)).lower().replace("-", "_").replace(" ", "_")
    if k in SENSITIVE_KEYS:
        return True
    return any(k.endswith(sfx) for sfx in SENSITIVE_SUFFIXES)


def redact(obj):
    """Return a deep copy of obj with sensitive values replaced by [REDACTED].

    Non-mutating: the caller's original object is untouched (the engine reuses
    result.data for metrics/verification after persisting it). Recurses through
    dicts and lists; scalars pass through unchanged.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _REDACTED if _is_sensitive(k) else redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact(v) for v in obj)
    return copy.copy(obj) if isinstance(obj, (set,)) else obj


def redact_json_str(s):
    """Redact a JSON-encoded DB string column. Returns a JSON string.

    Non-JSON / empty values are returned unchanged. Used on the serve path
    where step.result / job.inputs are stored as JSON text, so recursing over
    the row dict alone would not reach secrets nested inside the string.
    """
    if not s or not isinstance(s, str):
        return s
    try:
        return json.dumps(redact(json.loads(s)))
    except (ValueError, TypeError):
        return s


def redact_row(row: dict, json_columns=("result", "inputs", "verification", "error")) -> dict:
    """Redact a DB row dict for the serve path: scrub top-level sensitive keys
    and re-serialize any JSON-encoded columns with their secrets removed."""
    out = redact(row)
    for col in json_columns:
        if col in out:
            out[col] = redact_json_str(out[col])
    return out
