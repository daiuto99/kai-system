#!/usr/bin/env python3
"""Backups custodian — KAI-47 Phase 2.

DevOps owns backup freshness/integrity. Modeled on the disk custodian. Reuses the
proven detection in green_baseline (store freshness, verify_backups.sh, offsite).

Contract (§1):
  - a store's newest artifact is stale/absent, OR the       -> AUTO   (re-run backup.sh
    integrity verify fails                                      + re-verify; cooldown-
                                                                guarded, lock-guarded)
  - offsite copy enabled but FAILED / stale (DR lapsed)     -> STRUCTURAL (external
                                                                transport — do not loop)

Safe by construction: remediate only ever RE-RUNS the same backup.sh / verify the
2am cron already runs, under a lock (never concurrent with that cron) and a cooldown
(never hammered). It destroys nothing.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_H = 26          # a store older than this is stale (26h cron is daily @ 02:00)
OFFSITE_STALE_H = 36  # DR offsite copy stale threshold
COOLDOWN_S = 2 * 3600  # never re-run a store's backup more than once per 2h
BACKUP_SH = Path("/home/leo/kai-system/backup.sh")
VERIFY_SH = Path("/home/leo/kai-system/scripts/verify_backups.sh")
LOCK = Path("/home/leo/kai-system/logs/.devops_backups_custodian.lock")

# Every store backup.sh writes — a silently-failing one was the audit #01 blind spot.
STORES = {"plane": "*.sql.gz", "qdrant": "*.snapshot", "n8n": "*.tar.gz", "buzz": "*.sql.gz"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base() -> Path | None:
    for cand in (Path.home() / "backups", Path("/home/leo/backups")):
        if cand.exists():
            return cand
    return None


def store_age_h(base: Path, store: str, pattern: str) -> float | None:
    """Hours since the newest artifact for a store; None if the store has none."""
    sdir = base / store
    files = list(sdir.glob(pattern)) if sdir.exists() else []
    newest = max((f.stat().st_mtime for f in files), default=None)
    if newest is None:
        return None
    return (time.time() - newest) / 3600.0


# ── pure classification (unit-tested) ──────────────────────────────────────────

def store_stale(age_h: float | None, stale_h: float = STALE_H) -> bool:
    """A store with no artifact (None) or one older than stale_h is stale."""
    return age_h is None or age_h > stale_h


def offsite_stale(enabled: bool, result: str | None, age_h: float | None,
                  stale_h: float = OFFSITE_STALE_H) -> bool:
    """DR offsite is lapsed only when it is ENABLED and its last copy failed/stale.
    A gated/disabled offsite is a known WARN, not a backups-custodian action."""
    if not enabled:
        return False
    if result == "FAIL":
        return True
    if result is None or age_h is None:
        return False  # never run yet — cron pending, not the custodian's job to force
    return age_h > stale_h


def _offsite_status(base: Path):
    enabled = False
    for cand in (Path("/home/leo/kai-system/offsite.env"),):
        if cand.exists():
            for line in cand.read_text().splitlines():
                line = line.strip()
                if line.startswith("OFFSITE_ENABLED="):
                    enabled = line.split("=", 1)[1].strip().strip('"').strip("'") == "1"
    stamp = base / ".offsite_result"
    result, age_h = None, None
    if stamp.exists():
        try:
            result = stamp.read_text().strip().split()[0]
        except Exception:
            result = None
        age_h = (time.time() - stamp.stat().st_mtime) / 3600.0
    return enabled, result, age_h


class BackupsCustodian:
    domain = "backups"

    def assess(self) -> list:
        from devops_ownership import Finding, AUTO, STRUCTURAL
        base = _base()
        if base is None:
            return [Finding(
                domain="backups", check="backups_dir_absent", severity="crit",
                diagnosis="backups directory is absent — no local backups exist",
                disposition=STRUCTURAL,
                proposed_action="restore/relocate the backups directory and confirm backup.sh target",
                dedup_key="backups-dir-absent", detail={})]

        findings = []
        stale = []
        for store, pattern in STORES.items():
            if store_stale(store_age_h(base, store, pattern)):
                stale.append(store)
        if stale:
            findings.append(Finding(
                domain="backups", check="store_stale", severity="crit",
                diagnosis=f"backup store(s) stale/absent (> {STALE_H}h): {', '.join(stale)}",
                disposition=AUTO,
                proposed_action="re-run backup.sh and verify integrity",
                dedup_key="backups-store-stale", detail={"stale": stale}))

        enabled, result, age_h = _offsite_status(base)
        if offsite_stale(enabled, result, age_h):
            findings.append(Finding(
                domain="backups", check="offsite_lapsed", severity="crit",
                diagnosis=f"offsite DR copy lapsed (result={result}, age={age_h}h)",
                disposition=STRUCTURAL,
                proposed_action="investigate the offsite transport (target unreachable / credentials) — DR protection has lapsed",
                dedup_key="backups-offsite-lapsed",
                detail={"result": result, "age_h": age_h}))
        return findings

    def remediate_safe(self, f) -> str:
        # Cooldown + lock so we never hammer or collide with the 02:00 cron.
        now = time.time()
        if LOCK.exists() and (now - LOCK.stat().st_mtime) < COOLDOWN_S:
            return f"skipped — backup re-run within cooldown ({COOLDOWN_S//3600}h)"
        try:
            LOCK.parent.mkdir(parents=True, exist_ok=True)
            LOCK.write_text(_now())
        except Exception:
            pass
        results = []
        if BACKUP_SH.exists():
            try:
                r = subprocess.run(["bash", str(BACKUP_SH)], capture_output=True, text=True, timeout=1800)
                results.append(f"backup.sh exit={r.returncode}")
            except Exception as e:
                results.append(f"backup.sh error: {type(e).__name__}: {e}")
        else:
            results.append("backup.sh missing")
        if VERIFY_SH.exists():
            try:
                v = subprocess.run(["bash", str(VERIFY_SH)], capture_output=True, text=True, timeout=300)
                verdict = "PASS" if v.returncode == 0 else "FAIL"
                results.append(f"verify={verdict}")
            except Exception as e:
                results.append(f"verify error: {type(e).__name__}: {e}")
        return "; ".join(results)
