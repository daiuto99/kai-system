import context_service


def test_tier5_resolves_advisor_and_registry_specialist_personas():
    advisor = context_service.tier5_standing_context("kai")
    specialist = context_service.tier5_standing_context("architect")

    assert "error" not in advisor
    assert "error" not in specialist
    assert "persona" in specialist["blocks"]
