"""Tests for S5R-19: tiered spend-cap — interactive never hard-blocks."""
import json
import datetime
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_usage(cost_usd: float, calls_this_hour: int = 0) -> dict:
    today = datetime.date.today().isoformat()
    hour_key = datetime.datetime.now().strftime("%H")
    return {
        "days": [{
            "date": today,
            "cost_usd": cost_usd,
            "hours": {hour_key: {"calls": calls_this_hour}},
        }]
    }


def _rl(cost_usd: float, calls: int = 0, traffic_type: str = "interactive"):
    """Call _check_rate_limit with a mocked token_usage.json."""
    usage = json.dumps(_make_usage(cost_usd, calls))
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = usage

    import council_config as cc
    with patch.object(cc, "_maybe_alert"), \
         patch("council_config.Path", return_value=mock_path):
        # Path is called with the usage file path inside _check_rate_limit
        # We need to patch just the usage_path resolution
        real_path = cc.Path
        def patched_path(p):
            if "token_usage" in str(p):
                return mock_path
            return real_path(p)
        with patch("council_config.Path", side_effect=patched_path):
            return cc._check_rate_limit("kai", traffic_type=traffic_type)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestInteractiveTraffic:
    def test_below_warn_threshold_passes(self):
        result = _rl(cost_usd=2.00)
        assert result["blocked"] is False
        assert result["degrade"] is False
        assert result["warn"] is False

    def test_at_80_percent_warns_not_blocks(self):
        result = _rl(cost_usd=3.20)  # exactly 80% of $4.00
        assert result["blocked"] is False
        assert result["degrade"] is False
        assert result["warn"] is True

    def test_above_warn_still_warns(self):
        result = _rl(cost_usd=3.50)
        assert result["blocked"] is False
        assert result["warn"] is True

    def test_at_interactive_budget_degrades_not_blocks(self):
        result = _rl(cost_usd=4.00)
        assert result["blocked"] is False
        assert result["degrade"] is True
        assert result["warn"] is False

    def test_above_interactive_budget_degrades_not_blocks(self):
        result = _rl(cost_usd=4.80)
        assert result["blocked"] is False
        assert result["degrade"] is True

    def test_at_total_cap_still_not_blocked(self):
        """Interactive traffic is NEVER hard-blocked by the daily cost cap."""
        result = _rl(cost_usd=5.00)
        assert result["blocked"] is False
        assert result["degrade"] is True

    def test_above_total_cap_still_not_blocked(self):
        result = _rl(cost_usd=6.00)
        assert result["blocked"] is False


class TestAlertTraffic:
    def test_below_cap_passes(self):
        result = _rl(cost_usd=2.00, traffic_type="alert")
        assert result["blocked"] is False
        assert result["degrade"] is False

    def test_at_total_cap_not_blocked(self):
        """Alert traffic never hard-blocks regardless of spend."""
        result = _rl(cost_usd=5.00, traffic_type="alert")
        assert result["blocked"] is False

    def test_above_total_cap_not_blocked(self):
        result = _rl(cost_usd=8.00, traffic_type="alert")
        assert result["blocked"] is False


class TestHourlyCapHardBlock:
    def test_hourly_cap_blocks_interactive(self):
        result = _rl(cost_usd=0.50, calls=50)
        assert result["blocked"] is True
        assert "Hourly" in result["reason"]

    def test_hourly_cap_blocks_alert(self):
        result = _rl(cost_usd=0.50, calls=50, traffic_type="alert")
        assert result["blocked"] is True

    def test_below_hourly_cap_passes(self):
        result = _rl(cost_usd=0.50, calls=49)
        assert result["blocked"] is False


class TestConstants:
    def test_budget_tiers_sum_to_total(self):
        import council_config as cc
        assert cc.INTERACTIVE_BUDGET_USD + cc.ALERT_BUDGET_USD == cc.DAILY_COST_CAP_USD

    def test_warn_threshold_is_80_percent(self):
        import council_config as cc
        assert cc.WARN_THRESHOLD == 0.80
