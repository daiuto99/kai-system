import logging
from council_config import WORKER_URL

logger = logging.getLogger(__name__)


def handle_create_task(tool_input: dict, client) -> dict:
    r = client.post(f"{WORKER_URL}/tasks", json=tool_input)
    return r.json()
