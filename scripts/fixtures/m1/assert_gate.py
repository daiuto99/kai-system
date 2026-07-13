#!/usr/bin/env python3
"""Assert M1 scoping from real chat responses and persisted assembly logs."""
import json
import sys
from pathlib import Path


ALPHA_FACT = "m1-alpha-fact-001"
ALPHA_OTHER_TASK_FACT = "m1-alpha-other-task-fact-001"
BETA_FACT = "m1-beta-fact-001"


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    if len(sys.argv) != 7:
        raise SystemExit(
            "usage: assert_gate.py alpha-response alpha-log beta-response beta-log "
            "unscoped-response unscoped-log"
        )

    alpha_response, alpha_log, beta_response, beta_log, unscoped_response, unscoped_log = (
        load(path) for path in sys.argv[1:]
    )

    assert alpha_response["package_id"] == alpha_log["package_id"]
    assert beta_response["package_id"] == beta_log["package_id"]
    assert unscoped_response["package_id"] == unscoped_log["package_id"]

    alpha_facts = set(alpha_log["tiers"]["t4"]["facts"])
    beta_facts = set(beta_log["tiers"]["t4"]["facts"])
    unscoped_facts = set(unscoped_log["tiers"]["t4"]["facts"])

    assert ALPHA_FACT in alpha_facts
    assert ALPHA_OTHER_TASK_FACT not in alpha_facts
    assert BETA_FACT not in alpha_facts
    assert BETA_FACT in beta_facts
    assert ALPHA_FACT not in beta_facts
    assert ALPHA_OTHER_TASK_FACT not in beta_facts
    assert {ALPHA_FACT, ALPHA_OTHER_TASK_FACT, BETA_FACT} <= unscoped_facts

    # M1 deliberately does not alter Tier 3 filtering or its collection
    # allowlist. Project differentiation is therefore proven on Tier 4 only.
    assert alpha_log["tiers"]["t3"]["hits"] == []
    assert beta_log["tiers"]["t3"]["hits"] == []
    assert unscoped_log["tiers"]["t3"]["hits"] == []

    key_tuples = {
        tuple(alpha_log["key_tuple"]),
        tuple(beta_log["key_tuple"]),
        tuple(unscoped_log["key_tuple"]),
    }
    assert key_tuples == {("m1smoke", "m1-smoke-gate", None, None)}

    print(
        json.dumps(
            {
                "assembly_scope_gate": "PASS",
                "alpha_package_id": alpha_response["package_id"],
                "alpha_fixture_facts": sorted(
                    {ALPHA_FACT, ALPHA_OTHER_TASK_FACT, BETA_FACT} & alpha_facts
                ),
                "beta_package_id": beta_response["package_id"],
                "beta_fixture_facts": sorted(
                    {ALPHA_FACT, ALPHA_OTHER_TASK_FACT, BETA_FACT} & beta_facts
                ),
                "unscoped_package_id": unscoped_response["package_id"],
                "unscoped_fixture_facts": sorted(
                    {ALPHA_FACT, ALPHA_OTHER_TASK_FACT, BETA_FACT} & unscoped_facts
                ),
                "tier3_project_scoping": "NOT IMPLEMENTED; t3.hits empty for m1smoke",
                "no_default_project": True,
                "task_type_filtered_alpha_decoy": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
