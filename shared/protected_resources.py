"""KAI-1480 Phase 1 · Protected stateful-resource verifier (pure, no side effects).

The 2026-09-12 incident (KAI-1475) class: a `compose up` silently rebound buzz-minio
to a FRESH empty volume — the data didn't get deleted by an API call, it "vanished via
rebind" — and every monitor stayed green because nothing checked that each protected
store still (a) exists, (b) is bound to the RIGHT container at the RIGHT mount, and
(c) holds its expected data. This module is the single source of truth for that check.

It is deliberately pure: only docker/subprocess + urllib, NO notify import, so it can be
called from BOTH the every-minute custodian (which pages #devops) and the green baseline
(which turns RED). Creds never reach the host or process args (L18): each store is queried
INSIDE its own container using that container's own env.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "protected_resources.json"


def load_registry(path: Path | None = None) -> dict:
    return json.loads((path or REGISTRY_PATH).read_text())


def _run(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _volume_exists(name: str) -> bool:
    rc, _, _ = _run(["docker", "volume", "inspect", name], timeout=10)
    return rc == 0


def _container_mounts(container: str) -> list[dict] | None:
    """Return the container's Mounts (works on stopped containers too), or None if absent."""
    rc, out, _ = _run(["docker", "inspect", "--format", "{{json .Mounts}}", container], timeout=10)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _container_running(container: str) -> bool:
    rc, out, _ = _run(["docker", "inspect", "-f", "{{.State.Running}}", container], timeout=10)
    return rc == 0 and out.strip() == "true"


# ── invariant checks (each returns "" on pass, or a failure reason) ────────────
def _inv_minio_buckets(container: str, spec: dict) -> str:
    required = set(spec.get("buckets", []))
    script = (
        'mc alias set loc http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" '
        ">/dev/null 2>&1 && mc ls loc"
    )
    rc, out, err = _run(["docker", "exec", container, "sh", "-c", script], timeout=20)
    if rc != 0:
        return f"MinIO unreadable ({(err or out)[:80]})"
    present = {ln.rstrip("/").split()[-1] for ln in out.splitlines() if ln.strip()}
    missing = required - present
    return f"buckets missing {sorted(missing)}" if missing else ""


def _inv_pg_tables_min(container: str, spec: dict) -> str:
    minimum = int(spec.get("min", 1))
    script = (
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
        "\"select count(*) from information_schema.tables where table_schema='public'\""
    )
    rc, out, err = _run(["docker", "exec", container, "sh", "-c", script], timeout=20)
    if rc != 0:
        return f"Postgres unreadable ({(err or out)[:80]})"
    try:
        n = int(out.strip() or "0")
    except ValueError:
        return f"Postgres table count unparseable ({out[:40]})"
    return f"{n} tables < required {minimum} (empty/fresh volume)" if n < minimum else ""


def _inv_redis_ping(container: str, spec: dict) -> str:
    rc, out, err = _run(["docker", "exec", container, "redis-cli", "ping"], timeout=10)
    if rc != 0:
        return f"Redis unreadable ({(err or out)[:80]})"
    return "" if out.strip().upper() == "PONG" else f"PING returned {out.strip()!r}"


def _inv_qdrant_collections_min(container: str, spec: dict) -> str:
    minimum = int(spec.get("min", 1))
    url = spec.get("url", "http://localhost:6333/collections")
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            cols = json.loads(r.read())["result"]["collections"]
    except Exception as exc:  # noqa: BLE001 - report any failure as drift
        return f"Qdrant unreadable ({type(exc).__name__})"
    return f"{len(cols)} collections < required {minimum} (vector store empty)" if len(cols) < minimum else ""


_INVARIANTS = {
    "minio_buckets": _inv_minio_buckets,
    "pg_tables_min": _inv_pg_tables_min,
    "redis_ping": _inv_redis_ping,
    "qdrant_collections_min": _inv_qdrant_collections_min,
}


def verify_resource(res: dict) -> list[str]:
    """Return a list of failure strings for one registered resource ([] == intact)."""
    name = res.get("name", "?")
    container = res.get("container", "?")
    mount = res.get("mount", "?")
    fails: list[str] = []

    if not _volume_exists(name):
        fails.append(f"{name}: volume MISSING")

    mounts = _container_mounts(container)
    if mounts is None:
        fails.append(f"{name}: owning container '{container}' not found")
    else:
        match = next((m for m in mounts if m.get("Name") == name), None)
        if match is None:
            bound = sorted({m.get("Name") for m in mounts if m.get("Name")})
            fails.append(f"{name}: NOT bound to '{container}' (rebind/unbound; bound={bound})")
        elif match.get("Destination") != mount:
            fails.append(f"{name}: mounted at '{match.get('Destination')}', expected '{mount}'")

    inv = res.get("invariant") or {}
    inv_type = inv.get("type")
    if inv_type:
        if not _container_running(container):
            fails.append(f"{name}: container '{container}' not running (invariant unverified)")
        else:
            fn = _INVARIANTS.get(inv_type)
            if fn is None:
                fails.append(f"{name}: unknown invariant type '{inv_type}'")
            else:
                reason = fn(container, inv)
                if reason:
                    fails.append(f"{name}: {reason}")
    return fails


def verify_all(path: Path | None = None) -> tuple[list[str], int]:
    """Verify every registered resource. Returns (failures, resource_count)."""
    reg = load_registry(path)
    resources = reg.get("resources", [])
    failures: list[str] = []
    for res in resources:
        failures.extend(verify_resource(res))
    return failures, len(resources)


if __name__ == "__main__":
    fails, n = verify_all()
    if fails:
        print(f"PROTECTED-RESOURCE DRIFT ({len(fails)} across {n} resources):")
        for f in fails:
            print("  RED", f)
        raise SystemExit(1)
    print(f"all {n} protected resources intact")
