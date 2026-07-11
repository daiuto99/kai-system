import logging
from council_config import WORKER_URL, _worker_auth

logger = logging.getLogger(__name__)


def handle_create_task(tool_input: dict, client) -> dict:
    # Bug 48f85706: worker authenticates all routes. Per-request auth here is
    # explicit even though the shared client is authed — keeps the call site
    # self-evidently credentialed.
    r = client.post(f"{WORKER_URL}/tasks", json=tool_input, auth=_worker_auth())
    return r.json()
