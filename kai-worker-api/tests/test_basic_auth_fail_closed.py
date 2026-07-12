"""F1 regression: every malformed server credential shape fails closed."""

import base64
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as worker_main  # noqa: E402


class TestMalformedServerCredentialFailsClosed(unittest.TestCase):
    def test_all_malformed_shapes_return_503(self):
        original = worker_main._AUTH_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auth_file = Path(tmp) / "worker_auth.txt"
                worker_main._AUTH_FILE = auth_file
                client = TestClient(worker_main.app)

                cases = {
                    "empty": "",
                    "no_colon": "malformed",
                    "empty_user_and_password": ":",
                    "empty_password": "kai:",
                    "empty_user": ":pw",
                }
                for label, value in cases.items():
                    with self.subTest(label=label):
                        auth_file.write_text(value)
                        encoded = base64.b64encode(value.encode()).decode()
                        response = client.get(
                            "/system/ops-state",
                            headers={"Authorization": f"Basic {encoded}"},
                        )
                        self.assertEqual(response.status_code, 503)

                auth_file.unlink()
                self.assertEqual(client.get("/system/ops-state").status_code, 503)
        finally:
            worker_main._AUTH_FILE = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
