from unittest import mock

import execute_tool
import router


class _Response:
    def __init__(self, package):
        self.package = package

    def raise_for_status(self):
        return None

    def json(self):
        return {"package": self.package}


def _package():
    return {
        "package_id": "consult-package",
        "stable_text": "SPECIALIST PERSONA",
        "volatile_text": "GLOBAL CONTEXT",
        "facts_text": '<verified_fact id="seeded-architect-fact">Seeded architect fact</verified_fact>',
        "recall_text": '<recalled source="qdrant:architect">Seeded recall</recalled>',
        "messages": [],
        "budget_report": {"t3": {"hits": [{"source_collection": "architect"}]},
                          "t4": {"facts": ["seeded-architect-fact"]}},
    }


def test_cross_domain_consult_without_project_uses_memory_package_and_trail(monkeypatch):
    package = _package()
    post = mock.Mock(return_value=_Response(package))
    loop = mock.Mock(return_value=("specialist answer", 1, 2, 0, 0))
    tracked = mock.Mock()
    monkeypatch.setattr(execute_tool.fm, "get_specialist", lambda _id: {"name": "Architect", "domain": "systems"})
    monkeypatch.setattr(execute_tool.httpx, "post", post)
    monkeypatch.setattr(router, "_run_agentic_loop", loop)
    monkeypatch.setattr("council_config._track_usage", tracked)

    result = execute_tool._consult_specialist("architect", "Review the cross-domain request", "", None)

    assert "project" not in post.call_args.kwargs["json"]
    assert "seeded-architect-fact" in loop.call_args.args[3]
    assert result["assembly"]["project_scope"] is None
    assert result["assembly"]["tier4_fact_ids"] == ["seeded-architect-fact"]


def test_specialist_handler_uses_server_owned_message_project(monkeypatch):
    consult = mock.Mock(return_value={"ok": True})
    monkeypatch.setattr(execute_tool, "_consult_specialist", consult)

    execute_tool._h_specialists(None, "consult_specialist", {
        "specialist": "architect", "question": "Review", "_active_project": "kai-system",
    }, "kai")

    assert consult.call_args.kwargs["active_project"] == "kai-system"


class _MonkeyPatch:
    def set(self, target, name, value=None):
        if isinstance(target, str) and value is None:
            value = name
            target, name = target.rsplit(".", 1)
            import importlib
            target = importlib.import_module(target)
            setattr(target, name, value)
            return
        if isinstance(target, str):
            import importlib
            target, name = target.rsplit(".", 1)
            target = importlib.import_module(target)
        setattr(target, name, value)

    setattr = set


if __name__ == "__main__":
    tests = (
        test_cross_domain_consult_without_project_uses_memory_package_and_trail,
        test_specialist_handler_uses_server_owned_message_project,
    )
    for test in tests:
        test(_MonkeyPatch())
        print(f"PASS {test.__name__}")
