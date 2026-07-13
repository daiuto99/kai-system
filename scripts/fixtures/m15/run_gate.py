#!/usr/bin/env python3
"""Repeatable live gate for M1.5 null-advisor Fact Registry writes."""
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "scripts" / "fixtures" / "m15"
REGISTRY = Path("/home/leo/vault/00_System/registry/facts.json")
CAPTURE_DIR = Path(os.environ["CAPTURE_DIR"]) if os.environ.get("CAPTURE_DIR") else None
TEST_IDS = {
    "m15-global-general-001",
    "m15-global-project-001",
    "m15-advisor-general-001",
    "m15-advisor-project-001",
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def emit(label: str, value) -> None:
    print(label)
    print(json.dumps(value, indent=2, sort_keys=True))


def capture(name: str, value) -> None:
    if CAPTURE_DIR is None:
        return
    CAPTURE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CAPTURE_DIR.chmod(0o700)
    path = CAPTURE_DIR / name
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    path.chmod(0o600)


def registry_snapshot() -> dict:
    raw = REGISTRY.read_bytes()
    root = json.loads(raw)
    return {
        "sha256": sha256(raw),
        "facts_count": len(root["facts"]),
        "pre_existing_fact_intact": any(
            fact.get("id") == "fact-kai-system-topology-001"
            and fact.get("lifecycle") == "verified"
            for fact in root["facts"]
        ),
        "m15_fact_ids": sorted(
            fact.get("id") for fact in root["facts"] if fact.get("id") in TEST_IDS
        ),
    }


def locked_registry_bytes() -> bytes:
    lock_path = REGISTRY.with_name(f".{REGISTRY.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return REGISTRY.read_bytes()


def restore_registry(before_raw: bytes) -> dict:
    before = json.loads(before_raw)
    lock_path = REGISTRY.with_name(f".{REGISTRY.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        current_raw = REGISTRY.read_bytes()
        current = json.loads(current_raw)
        removed = sorted(
            fact.get("id") for fact in current["facts"] if fact.get("id") in TEST_IDS
        )
        candidate = dict(current)
        candidate["facts"] = [
            fact for fact in current["facts"] if fact.get("id") not in TEST_IDS
        ]
        if candidate != before:
            raise RuntimeError(
                "cleanup refused: non-M1.5 registry data changed during the gate"
            )

        mode = stat.S_IMODE(REGISTRY.stat().st_mode)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{REGISTRY.name}.m15-cleanup.", suffix=".tmp", dir=REGISTRY.parent
        )
        try:
            with os.fdopen(fd, "wb") as temp_file:
                os.fchmod(temp_file.fileno(), mode)
                temp_file.write(before_raw)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, REGISTRY)
            directory_fd = os.open(REGISTRY.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    result = {
        "cleanup": "PASS",
        "removed_ids": removed,
        "sha256_before": sha256(before_raw),
        "sha256_after": sha256(REGISTRY.read_bytes()),
    }
    if result["sha256_before"] != result["sha256_after"]:
        raise AssertionError(result)
    emit("REGISTRY CLEANUP", result)
    capture("registry-cleanup.json", result)
    return result


def run_cli(label: str, *arguments: str, expect_success: bool = True) -> dict:
    command = ["python3", str(ROOT / "scripts" / "ingest.py"), *arguments]
    print(f"COMMAND {label}: {' '.join(command)}")
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    result = {
        "label": label,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    emit(f"RESULT {label}", result)
    capture(f"{label}.json", result)
    if expect_success and completed.returncode != 0:
        raise AssertionError(result)
    if not expect_success and completed.returncode == 0:
        raise AssertionError(result)
    return result


def ingest_fixture(label: str, filename: str, *scope: str) -> dict:
    result = run_cli(
        label,
        "--facts",
        str(FIXTURE / filename),
        *scope,
        "--ingested-by",
        "m15-live-gate",
    )
    return json.loads(result["stdout"])


def validate_live_scopes() -> dict:
    root = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {fact.get("id"): fact for fact in root["facts"]}
    expected = {
        "m15-global-general-001": (None, None, None),
        "m15-global-project-001": (None, "testproj", "testtype"),
        "m15-advisor-general-001": ("m15scope", None, None),
        "m15-advisor-project-001": ("m15scope", "testproj", "testtype"),
    }
    for fact_id, scopes in expected.items():
        fact = by_id[fact_id]
        actual = (fact.get("advisor"), fact.get("project"), fact.get("task_type"))
        if actual != scopes:
            raise AssertionError({"fact_id": fact_id, "expected": scopes, "actual": actual})
        if fact.get("ingested_by") != "m15-live-gate":
            raise AssertionError(f"missing provenance on {fact_id}")
        if fact.get("lifecycle") != "verified":
            raise AssertionError(f"unverified lifecycle on {fact_id}")
    result = {
        "scope_matrix": "PASS",
        "stored_scopes": {fact_id: list(scopes) for fact_id, scopes in expected.items()},
        "pre_existing_fact_intact": any(
            fact.get("id") == "fact-kai-system-topology-001"
            and fact.get("lifecycle") == "verified"
            for fact in root["facts"]
        ),
    }
    if not result["pre_existing_fact_intact"]:
        raise AssertionError(result)
    emit("LIVE SCOPE MATRIX", result)
    capture("scope-matrix.json", result)
    return result


def assemble(label: str, advisor: str, project: str | None = None, task_type: str | None = None):
    payload = {
        "key": {"advisor": advisor, "device": f"m15-live-gate-{label}"},
        "message": "What are the verified M1.5 aurora lantern and project markers?",
    }
    if project is not None:
        payload["project"] = project
    if task_type is not None:
        payload["task_type"] = task_type
    command = [
        "docker", "exec", "-i", "kai-orchestrator", "curl", "-fsS", "-X", "POST",
        "http://localhost:8003/context/assemble", "-H", "Content-Type: application/json",
        "--data-binary", "@-",
    ]
    completed = subprocess.run(
        command, input=json.dumps(payload), text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError({"label": label, "stderr": completed.stderr})
    response = json.loads(completed.stdout)
    package_id = response["package"]["package_id"]
    log_command = [
        "docker", "exec", "kai-orchestrator", "python",
        "/kai-system/scripts/m0_read_assembly_log.py", package_id,
    ]
    log_result = subprocess.run(log_command, text=True, capture_output=True, check=False)
    if log_result.returncode != 0:
        raise AssertionError({"label": label, "stderr": log_result.stderr})
    assembly_log = json.loads(log_result.stdout)
    summary = {
        "label": label,
        "advisor": advisor,
        "project": project,
        "task_type": task_type,
        "package_id": package_id,
        "tier4_fact_ids": response["package"]["budget_report"]["t4"]["facts"],
    }
    emit(f"ASSEMBLE {label}", summary)
    emit(f"ASSEMBLY LOG {label}", assembly_log)
    capture(f"{label}-response.json", response)
    capture(f"{label}-assembly-log.json", assembly_log)
    return response, assembly_log


def run_gate(before_raw: bytes) -> dict:
    before = registry_snapshot()
    emit("REGISTRY BEFORE", before)
    capture("registry-before.json", before)
    if before["m15_fact_ids"]:
        raise AssertionError("pre-gate registry already contains M1.5 throwaway IDs")
    if not before["pre_existing_fact_intact"]:
        raise AssertionError("pre-existing topology fact missing before gate")

    ingest_fixture("write-global-general", "global_general.json", "--global")
    ingest_fixture(
        "write-global-project", "global_project.json", "--global",
        "--project", "testproj", "--task-type", "testtype",
    )
    ingest_fixture("write-advisor-general", "advisor_general.json", "--advisor", "m15scope")
    ingest_fixture(
        "write-advisor-project", "advisor_project.json", "--advisor", "m15scope",
        "--project", "testproj", "--task-type", "testtype",
    )
    validate_live_scopes()

    before_provenance = sha256(REGISTRY.read_bytes())
    provenance = run_cli(
        "missing-ingested-by",
        "--facts", str(FIXTURE / "global_general.json"), "--global",
        expect_success=False,
    )
    after_provenance = sha256(REGISTRY.read_bytes())
    if before_provenance != after_provenance or "--ingested-by is required" not in provenance["stderr"]:
        raise AssertionError("missing provenance did not fail closed")

    before_invalid = sha256(REGISTRY.read_bytes())
    invalid = run_cli(
        "malformed-global",
        "--facts", str(FIXTURE / "invalid_global.json"), "--global",
        "--ingested-by", "m15-live-gate",
        expect_success=False,
    )
    after_invalid = sha256(REGISTRY.read_bytes())
    fail_closed = {
        "exit_code": invalid["exit_code"],
        "sha256_before": before_invalid,
        "sha256_after": after_invalid,
        "unchanged": before_invalid == after_invalid,
    }
    if invalid["exit_code"] != 2 or not fail_closed["unchanged"]:
        raise AssertionError(fail_closed)
    emit("FAIL-CLOSED GLOBAL", fail_closed)
    capture("fail-closed.json", fail_closed)

    before_rerun = sha256(REGISTRY.read_bytes())
    rerun = ingest_fixture("idempotent-global-rerun", "global_general.json", "--global")
    after_rerun = sha256(REGISTRY.read_bytes())
    idempotency = {
        "added": rerun["added"],
        "already_present": rerun["already_present"],
        "sha256_before": before_rerun,
        "sha256_after": after_rerun,
        "byte_identical": before_rerun == after_rerun,
    }
    if rerun["added"] != 0 or not idempotency["byte_identical"]:
        raise AssertionError(idempotency)
    emit("IDEMPOTENCY", idempotency)
    capture("idempotency.json", idempotency)

    roads, roads_log = assemble("roads-global", "roads")
    sky, sky_log = assemble("sky-global", "sky")
    project_match, match_log = assemble(
        "roads-project-match", "roads", project="testproj", task_type="testtype"
    )
    project_mismatch, mismatch_log = assemble(
        "roads-project-mismatch", "roads", project="otherproj", task_type="testtype"
    )
    general_id = "m15-global-general-001"
    project_id = "m15-global-project-001"
    advisor_ids = {"m15-advisor-general-001", "m15-advisor-project-001"}
    roads_ids = set(roads["package"]["budget_report"]["t4"]["facts"])
    sky_ids = set(sky["package"]["budget_report"]["t4"]["facts"])
    match_ids = set(project_match["package"]["budget_report"]["t4"]["facts"])
    mismatch_ids = set(project_mismatch["package"]["budget_report"]["t4"]["facts"])
    if general_id not in roads_ids or general_id not in sky_ids:
        raise AssertionError("global fact missing from Roads or Sky")
    if project_id not in match_ids or project_id in mismatch_ids:
        raise AssertionError("global project scope did not apply null-or-exact matching")
    if advisor_ids & (roads_ids | sky_ids | match_ids | mismatch_ids):
        raise AssertionError("throwaway advisor-scoped fact leaked to Roads/Sky")

    result = {
        "gate": "PASS",
        "roads_package_id": roads["package"]["package_id"],
        "sky_package_id": sky["package"]["package_id"],
        "project_match_package_id": project_match["package"]["package_id"],
        "project_mismatch_package_id": project_mismatch["package"]["package_id"],
        "dual_advisor_global_read": True,
        "project_null_or_exact": True,
        "fail_closed": True,
        "idempotent": True,
        "assembly_log_package_ids": [
            roads_log["package_id"], sky_log["package_id"],
            match_log["package_id"], mismatch_log["package_id"],
        ],
        "pre_gate_sha256": sha256(before_raw),
    }
    emit("M1.5 LIVE GATE", result)
    capture("gate-result.json", result)
    return result


def main() -> None:
    before_raw = locked_registry_bytes()
    result = None
    try:
        result = run_gate(before_raw)
    finally:
        restore_registry(before_raw)
    result["post_cleanup_sha256"] = sha256(REGISTRY.read_bytes())
    result["cleanup_sha_restored"] = result["post_cleanup_sha256"] == result["pre_gate_sha256"]
    if not result["cleanup_sha_restored"]:
        raise AssertionError(result)
    emit("M1.5 FINAL RESULT", result)
    capture("final-result.json", result)
    if CAPTURE_DIR is not None:
        print(f"FULL CAPTURE DIRECTORY: {CAPTURE_DIR}")


if __name__ == "__main__":
    main()
