"""container_images — registry-latest currency reader.

Pins the honesty invariants: a running image whose installed manifest-list
digest matches the upstream registry reads FRESH and uncaused; a behind image
reads STALE with a cause (Findings Contract); a locally-built compose image
(no upstream registry) is not-checked/n/a and NEVER a faked current or a false
stale; a registry read that fails for a real registry is not-checked WITH its
reason; and each unique image is resolved with a single registry call.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))

import currency_scan  # noqa: E402
import findings  # noqa: E402

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _runner(fleet, *, buildx_calls=None):
    """fleet: image -> {"local": sha|None, "remote": sha|None, "created": ts}.
    Emulates the `_run(args, timeout) -> (code, out, err)` contract."""
    def run(args, timeout=25):
        if args[:2] == ["docker", "ps"]:
            lines = "\n".join(f"c-{img.split('/')[-1].split(':')[0]}\t{img}" for img in fleet)
            return 0, lines, ""
        if args[:3] == ["docker", "image", "inspect"]:
            img, fmt = args[3], args[-1]
            spec = fleet.get(img, {})
            if "Created" in fmt:
                return 0, spec.get("created", "2026-01-01T00:00:00Z"), ""
            # {{json .RepoDigests}}
            local = spec.get("local")
            return 0, (f'["{img.split("@")[0]}@{local}"]' if local else "[]"), ""
        if args[:3] == ["docker", "buildx", "imagetools"]:
            img = args[4]  # ["docker","buildx","imagetools","inspect",<ref>,...]
            if buildx_calls is not None:
                buildx_calls.append(img)
            remote = fleet.get(img, {}).get("remote")
            if remote:
                return 0, remote, ""
            return 1, "", "ERROR: pull access denied, repository does not exist or may require authorization"
        return 0, "", ""
    return run


def test_matching_digest_is_fresh_and_uncaused():
    layer = currency_scan.read_container_images(
        runner=_runner({"postgres:17": {"local": SHA_A, "remote": SHA_A}}))
    comp = layer["components"][0]
    assert layer["status"] == "fresh"
    assert comp["status"] == "fresh" and comp["current"] is True and comp["latest"] == SHA_A
    assert "cause" not in layer and "cause" not in comp


def test_behind_image_is_stale_with_cause_and_satisfies_contract():
    layer = currency_scan.read_container_images(
        runner=_runner({"nginx:alpine": {"local": SHA_A, "remote": SHA_B}}))
    comp = layer["components"][0]
    assert layer["status"] == "stale" and comp["status"] == "stale" and comp["current"] is False
    assert comp["cause"] and layer["cause"]
    findings.assert_contract({"container_images": layer})  # the contract has teeth and passes


def test_local_build_is_not_checked_na_never_faked_or_false_stale():
    layer = currency_scan.read_container_images(
        runner=_runner({"kai-system-kai-worker-api": {"local": SHA_A, "remote": None}}))
    comp = layer["components"][0]
    assert comp["status"] == "not-checked" and comp["applicable"] is False
    assert comp["current"] is None  # honest: no faked current, and not a false stale
    assert layer["status"] == "not-checked" and "cause" not in layer


def test_registry_failure_on_real_registry_is_not_checked_with_reason():
    # has a registry domain -> a failed read is a real not-checked, NOT a local-build n/a
    layer = currency_scan.read_container_images(
        runner=_runner({"ghcr.io/berriai/litellm:main-latest": {"local": SHA_A, "remote": None}}))
    comp = layer["components"][0]
    assert comp["status"] == "not-checked" and comp.get("applicable") is not False
    assert "registry read failed" in comp["note"]


def test_mixed_fleet_rolls_up_to_stale_and_passes_contract():
    layer = currency_scan.read_container_images(runner=_runner({
        "postgres:17": {"local": SHA_A, "remote": SHA_A},                 # fresh
        "nginx:alpine": {"local": SHA_A, "remote": SHA_B},                # stale
        "kai-system-kai-orchestrator": {"local": SHA_A, "remote": None},  # local-build n/a
    }))
    assert layer["status"] == "stale" and layer["cause"]
    assert "1 stale / 2 compared" in layer["detail"] and "1 local-build" in layer["detail"]
    findings.assert_contract({"container_images": layer})


def test_duplicate_images_resolved_once():
    calls = []
    fleet = {"postgres:17": {"local": SHA_A, "remote": SHA_A}}
    run = _runner(fleet, buildx_calls=calls)

    def run_two_containers(args, timeout=25):
        if args[:2] == ["docker", "ps"]:
            return 0, "pg-1\tpostgres:17\npg-2\tpostgres:17", ""
        return run(args, timeout)

    layer = currency_scan.read_container_images(runner=run_two_containers)
    assert len(layer["components"]) == 2
    assert calls == ["postgres:17"]  # two containers, one registry call
