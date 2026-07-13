import json

import pytest

import registry


EXISTING_FACT = {
    "id": "fact-kai-system-topology-001",
    "advisor": "kai",
    "project": "kai-system",
    "task_type": None,
    "domain": "infrastructure",
    "key": "kai-system_authoritative_repo",
    "value": "The authoritative deploy repo is /home/leo/kai-system.",
    "lifecycle": "verified",
    "source": "Leo-confirmed",
    "updated_at": "2026-07-11T17:00:00Z",
}


def _registry_file(tmp_path):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({"_note": "legacy registry", "facts": [EXISTING_FACT]}, indent=2))
    return path


def test_invalid_batch_is_rejected_without_registry_mutation(tmp_path):
    path = _registry_file(tmp_path)
    before = path.read_bytes()

    with pytest.raises(registry.RegistryValidationError, match="source must be"):
        registry.append_verified_facts(
            [
                {
                    "id": "m0-invalid",
                    "domain": "testing",
                    "key": "missing_source",
                    "value": "This input is deliberately invalid.",
                }
            ],
            advisor="m0smoke",
            project="m0-seed",
            task_type="registry-smoke",
            ingested_by="pytest",
            registry_path=path,
            ingested_at="2026-07-13T12:00:00Z",
        )

    assert path.read_bytes() == before
    assert json.loads(path.read_text())["facts"] == [EXISTING_FACT]


def test_append_preserves_legacy_read_path_and_adds_scoped_provenance(tmp_path, monkeypatch):
    path = _registry_file(tmp_path)
    monkeypatch.setattr(registry, "_REGISTRY_PATH", path)
    incoming = [
        {
            "id": "m0-smoke-fact-001",
            "domain": "testing",
            "key": "m0_registry_marker",
            "value": "The verified marker is silver-orchid-7319.",
            "source": "scripts/fixtures/m0/test_facts.json",
        }
    ]

    result = registry.append_verified_facts(
        incoming,
        advisor="m0smoke",
        project="m0-seed",
        task_type="registry-smoke",
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T12:00:00Z",
    )

    stored = json.loads(path.read_text())
    assert result["added"] == 1
    assert stored["_note"] == "legacy registry"
    assert stored["facts"][0] == EXISTING_FACT
    assert registry.facts_for("kai", project="kai-system") == [EXISTING_FACT]
    assert registry.facts_for("m0isolation", project="m0-seed", task_type="registry-smoke") == []

    added = registry.facts_for("m0smoke", project="m0-seed", task_type="registry-smoke")
    assert [fact["id"] for fact in added] == ["m0-smoke-fact-001"]
    assert added[0]["lifecycle"] == "verified"
    assert added[0]["ingested_at"] == "2026-07-13T12:00:00Z"
    assert added[0]["ingested_by"] == "pytest"
    assert added[0]["updated_at"] == "2026-07-13T12:00:00Z"

    before_rerun = path.read_bytes()
    rerun = registry.append_verified_facts(
        incoming,
        advisor="m0smoke",
        project="m0-seed",
        task_type="registry-smoke",
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T13:00:00Z",
    )
    assert rerun["added"] == 0
    assert rerun["already_present"] == ["m0-smoke-fact-001"]
    assert path.read_bytes() == before_rerun


def _scope_fact(fact_id, marker):
    return {
        "id": fact_id,
        "domain": "testing",
        "key": f"m15_{marker}",
        "value": f"M1.5 scope marker {marker}.",
        "source": "M1.5 pytest fixture",
    }


def _fact_ids(facts):
    return {fact["id"] for fact in facts}


def test_global_writer_covers_all_advisor_project_combinations_and_dual_advisor_read(
    tmp_path, monkeypatch
):
    path = _registry_file(tmp_path)
    monkeypatch.setattr(registry, "_REGISTRY_PATH", path)

    registry.append_verified_facts(
        [_scope_fact("m15-advisor-general", "advisor-general")],
        advisor="roads",
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T18:00:00Z",
    )
    registry.append_verified_facts(
        [_scope_fact("m15-global-general", "global-general")],
        advisor=None,
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T18:00:01Z",
    )
    registry.append_verified_facts(
        [_scope_fact("m15-global-project", "global-project")],
        advisor=None,
        project="testproj",
        task_type="testtype",
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T18:00:02Z",
    )
    registry.append_verified_facts(
        [_scope_fact("m15-advisor-project", "advisor-project")],
        advisor="roads",
        project="testproj",
        task_type="testtype",
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T18:00:03Z",
    )

    stored_by_id = {
        fact["id"]: fact for fact in json.loads(path.read_text())["facts"]
    }
    assert stored_by_id["m15-advisor-general"]["advisor"] == "roads"
    assert stored_by_id["m15-advisor-general"]["project"] is None
    assert stored_by_id["m15-global-general"]["advisor"] is None
    assert stored_by_id["m15-global-general"]["project"] is None
    assert stored_by_id["m15-global-project"]["advisor"] is None
    assert stored_by_id["m15-global-project"]["project"] == "testproj"
    assert stored_by_id["m15-global-project"]["task_type"] == "testtype"
    assert stored_by_id["m15-advisor-project"]["advisor"] == "roads"
    assert stored_by_id["m15-advisor-project"]["project"] == "testproj"
    assert stored_by_id["m15-advisor-project"]["task_type"] == "testtype"

    assert _fact_ids(
        registry.facts_for("roads", project="testproj", task_type="testtype")
    ) >= {
        "m15-advisor-general",
        "m15-global-general",
        "m15-global-project",
        "m15-advisor-project",
    }
    assert _fact_ids(
        registry.facts_for("sky", project="testproj", task_type="testtype")
    ) >= {
        "m15-global-general",
        "m15-global-project",
    }
    assert "m15-advisor-general" not in _fact_ids(
        registry.facts_for("sky", project="testproj", task_type="testtype")
    )
    assert _fact_ids(
        registry.facts_for("roads", project="otherproj", task_type="testtype")
    ) >= {
        "m15-advisor-general",
        "m15-global-general",
    }
    assert "m15-global-project" not in _fact_ids(
        registry.facts_for("roads", project="otherproj", task_type="testtype")
    )
    assert "m15-global-project" not in _fact_ids(
        registry.facts_for("sky", project="testproj", task_type="othertype")
    )
    # Reader contract: an omitted project does not filter on that dimension.
    assert "m15-global-project" in _fact_ids(registry.facts_for("sky"))


def test_global_rejections_are_fail_closed(tmp_path):
    path = _registry_file(tmp_path)
    before_malformed_batch = path.read_bytes()
    with pytest.raises(registry.RegistryValidationError, match="source must be"):
        registry.append_verified_facts(
            [
                {
                    "id": "m15-global-malformed",
                    "domain": "testing",
                    "key": "missing_source",
                    "value": "Malformed global test fact.",
                }
            ],
            advisor=None,
            ingested_by="pytest",
            registry_path=path,
            ingested_at="2026-07-13T18:01:00Z",
        )
    assert path.read_bytes() == before_malformed_batch

    fact = _scope_fact("m15-global-conflict", "original")
    registry.append_verified_facts(
        [fact],
        advisor=None,
        ingested_by="pytest",
        registry_path=path,
        ingested_at="2026-07-13T18:01:01Z",
    )
    before_conflict = path.read_bytes()
    conflicting = dict(fact, value="Different content under the same stable ID.")
    with pytest.raises(registry.RegistryValidationError, match="already exists"):
        registry.append_verified_facts(
            [conflicting],
            advisor=None,
            ingested_by="pytest",
            registry_path=path,
            ingested_at="2026-07-13T18:01:02Z",
        )
    assert path.read_bytes() == before_conflict

    malformed_registry = tmp_path / "malformed.json"
    malformed_registry.write_text('{"facts": "not-an-array"}')
    before_malformed_registry = malformed_registry.read_bytes()
    with pytest.raises(
        registry.RegistryValidationError,
        match="existing registry must be an object containing a facts array",
    ):
        registry.append_verified_facts(
            [
                _scope_fact(
                    "m15-global-existing-malformed", "existing-malformed"
                )
            ],
            advisor=None,
            ingested_by="pytest",
            registry_path=malformed_registry,
            ingested_at="2026-07-13T18:01:03Z",
        )
    assert malformed_registry.read_bytes() == before_malformed_registry
