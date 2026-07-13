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
