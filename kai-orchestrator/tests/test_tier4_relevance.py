import context_service


def _fact(fact_id, key, value, *, domain="equipment", advisor=None):
    return {
        "id": fact_id,
        "advisor": advisor,
        "project": None,
        "task_type": None,
        "domain": domain,
        "key": key,
        "value": value,
        "lifecycle": "verified",
        "source": "pytest",
        "updated_at": "2026-07-13T20:00:00Z",
    }


def test_late_query_relevant_global_facts_survive_tier4_budget(monkeypatch):
    """Regression for KAI-788: equal timestamps must not make file order win."""
    fillers = [
        _fact(
            f"early-unrelated-{index}",
            f"unrelated_item_{index}",
            (
                "This deliberately unrelated fact occupies an early registry position "
                f"for deterministic starvation coverage number {index}."
            ),
        )
        for index in range(7)
    ]
    facts = fillers + [
        _fact(
            "leo-primary-daw-001",
            "primary_daw",
            "Leo's primary DAW is Logic Pro, chosen for fast composition and production.",
            domain="studio",
        ),
        _fact(
            "leo-500-series-chain-001",
            "500_series_chain",
            "Leo's recording front end uses a 500-series chain before the audio interface.",
            domain="studio",
        ),
        _fact(
            "leo-number-one-guitar-001",
            "number_one_guitar",
            "Leo's number one main guitar is the Music Man Silhouette.",
        ),
    ]

    monkeypatch.setattr(
        context_service.registry,
        "facts_for",
        lambda advisor, project=None, task_type=None: list(facts),
    )
    monkeypatch.setattr(context_service.threat_scan, "scan_content", lambda *args, **kwargs: [])
    monkeypatch.setattr(context_service, "TIER4_CHAR_CAP", 520)

    studio = context_service._tier4_facts(
        "sky",
        None,
        None,
        "Which DAW and 500-series recording chain does Leo use?",
    )
    guitar = context_service._tier4_facts(
        "sky",
        None,
        None,
        "What is Leo's number one main guitar?",
    )

    assert "leo-primary-daw-001" in studio["facts"]
    assert "leo-500-series-chain-001" in studio["facts"]
    assert "leo-number-one-guitar-001" in guitar["facts"]
    assert studio["facts"] != guitar["facts"]

    # The same advisor:null candidates remain readable from both advisor views.
    roads = context_service._tier4_facts(
        "roads",
        None,
        None,
        "Which DAW and 500-series recording chain does Leo use?",
    )
    assert "leo-primary-daw-001" in roads["facts"]
    assert "leo-500-series-chain-001" in roads["facts"]
