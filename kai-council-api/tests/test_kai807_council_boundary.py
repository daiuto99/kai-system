"""KAI-807 regression: council must remain private and fail closed."""
from pathlib import Path

from fastapi.testclient import TestClient

import main


def test_unauthenticated_message_is_denied():
    # Middleware must reject before the paid/side-effecting route is entered.
    with TestClient(main.app) as client:
        response = client.post(
            "/council/message", json={"channel": "kai", "message": "test", "user_id": "test"}
        )
    assert response.status_code in (401, 503)


def test_council_port_is_not_host_published():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    council = compose.split("  kai-council-api:\n", 1)[1].split("\n  kai-slack-bot:", 1)[0]
    assert '"8002:8002"' not in council
