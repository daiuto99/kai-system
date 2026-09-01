import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from check_ruff_baseline import verify, write_baseline

import pytest

# Same dormancy as test_green_baseline: ci.sh runs this only via `pytest -m
# whole_repo`; without this marker the test was silently deselected.
# [test-wiring:green-baseline-dormant fix]
pytestmark = pytest.mark.whole_repo


class RuffBaselineTests(unittest.TestCase):
    def test_existing_finding_passes_and_new_finding_fails(self):
        finding = {"filename": "/app/example.py", "code": "F401", "location": {"row": 3, "column": 1}}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "ruff.json"
            baseline = root / "baseline.json"
            report.write_text(json.dumps([finding]))
            write_baseline(baseline, [("worker", report)])
            self.assertEqual(verify(baseline, [("worker", report)]), [])
            finding["location"] = {"row": 4, "column": 1}
            report.write_text(json.dumps([finding]))
            new = verify(baseline, [("worker", report)])
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["code"], "F401")
