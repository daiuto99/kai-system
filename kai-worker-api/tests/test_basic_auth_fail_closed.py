"""F1 regression: malformed server credentials fail closed on every source."""

import base64
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as worker_main  # noqa: E402


class TestMalformedServerCredentialFailsClosed(unittest.TestCase):
    cases = {"empty": "", "no_colon": "malformed", "empty_user_and_password": ":", "empty_password": "kai:", "empty_user": ":pw"}

    @staticmethod
    def _request(client: TestClient, value: str):
        encoded = base64.b64encode(value.encode()).decode()
        return client.get("/system/ops-state", headers={"Authorization": f"Basic {encoded}"})

    def test_malformed_docker_secret_does_not_fall_back_to_host_credential(self):
        original = worker_main._AUTH_FILES
        try:
            with tempfile.TemporaryDirectory() as tmp:
                docker_secret, host_fallback = Path(tmp) / "kai_worker_auth", Path(tmp) / "worker_auth.txt"
                host_fallback.write_text("host:valid-password")
                worker_main._AUTH_FILES = (docker_secret, host_fallback)
                client = TestClient(worker_main.app)
                for label, value in self.cases.items():
                    with self.subTest(label=label):
                        docker_secret.write_text(value)
                        self.assertEqual(self._request(client, "host:valid-password").status_code, 503)
        finally:
            worker_main._AUTH_FILES = original

    def test_malformed_host_fallback_returns_503(self):
        original = worker_main._AUTH_FILES
        try:
            with tempfile.TemporaryDirectory() as tmp:
                docker_secret, host_fallback = Path(tmp) / "missing_docker_secret", Path(tmp) / "worker_auth.txt"
                worker_main._AUTH_FILES = (docker_secret, host_fallback)
                client = TestClient(worker_main.app)
                for label, value in self.cases.items():
                    with self.subTest(label=label):
                        host_fallback.write_text(value)
                        self.assertEqual(self._request(client, "host:valid-password").status_code, 503)
                host_fallback.unlink()
                self.assertEqual(client.get("/system/ops-state").status_code, 503)
        finally:
            worker_main._AUTH_FILES = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
