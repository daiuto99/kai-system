import json
import autonomy_policy as policy


def test_leo_owned_reversible_action_is_autonomous(tmp_path, monkeypatch):
    p = tmp_path / "org.json"; p.write_text(json.dumps({"routing_rules":{"infrastructure_task":{"high_risk_threshold":["structural change"]}}}))
    monkeypatch.setattr(policy, "_ORG_MODEL_PATH", p)
    assert policy.classify({"op":"deploy_plugin", "owner":"leo"}).mode == "autonomous"


def test_external_and_high_risk_actions_require_approval(tmp_path, monkeypatch):
    p = tmp_path / "org.json"; p.write_text(json.dumps({"routing_rules":{"infrastructure_task":{"high_risk_threshold":["data migration"]}}}))
    monkeypatch.setattr(policy, "_ORG_MODEL_PATH", p)
    assert policy.classify({"op":"deploy_plugin", "owner":"client"}).mode == "approve"
    assert policy.classify({"op":"data migration", "owner":"leo"}).mode == "approve"
