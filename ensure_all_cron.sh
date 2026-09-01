#!/bin/bash
# KAI worker — master crontab installer (BUG 5cbe2b4a, idempotent + reproducible).
#
# The worker host crontab had NO version-controlled, reproducible installer for the
# FULL job set: a single bad `crontab -` on 2026-08-24 wiped all jobs, recoverable
# only by hand-reconstructing from ~8 days of syslog. This script makes the full set
# reproducible from worker_crontab.canonical (the committed source of truth).
#
# Modes:
#   --install (default)  Back up the live crontab, then install the canonical set
#                        wholesale (always from the committed file — never from a
#                        possibly-empty live read). Use for a fresh checkout or a
#                        full-wipe recovery.
#   --check              Compare the live crontab to canonical as a SET (order- and
#                        comment-insensitive). Exit 0 if they match, 1 on drift OR an
#                        unreadable/wiped crontab (never a false-clean). Prints what is
#                        missing/extra. No mutation.
#   --heal               If canonical lines are MISSING from the live crontab, back up
#                        and append them (never removes extras). REFUSES (exit 3) if the
#                        crontab is unreadable — it will not rebuild blind and risk
#                        dropping out-of-band lines; use --install to reprovision from
#                        a wipe. Idempotent; safe on a schedule alongside the per-line
#                        ensure_*.sh installers.
#   --print              Print the canonical job lines (comments/blanks stripped).
#
# CRONTAB_BIN overrides the `crontab` command (used by the test harness to sandbox).
set -euo pipefail

ROOT="/home/leo/kai-system"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${CANONICAL_FILE:-$SCRIPT_DIR/worker_crontab.canonical}"
BACKUP_DIR="${CRON_BACKUP_DIR:-/home/leo/backups}"
LOG="${ENSURE_ALL_CRON_LOG:-$ROOT/logs/ensure_all_cron.log}"
CRONTAB_BIN="${CRONTAB_BIN:-crontab}"
LOCKFILE="${ENSURE_ALL_CRON_LOCK:-$ROOT/.ensure_all_cron.lock}"

mode="${1:---install}"

log() { mkdir -p "$(dirname "$LOG")" 2>/dev/null || true; echo "[$(date '+%Y%m%d_%H%M%S')] $*" >> "$LOG" 2>/dev/null || true; }
die() { echo "ensure_all_cron: $*" >&2; exit 2; }

[ -f "$CANONICAL" ] || die "canonical file not found: $CANONICAL"

# Serialize mutating runs so a scheduled --heal and a manual --install can't interleave
# their read-modify-write of the crontab. Best-effort: the per-line ensure_*.sh do not
# share this lock, so a cross-installer race is still possible (pre-existing) but the
# window is sub-second and both only ADD their own canonical line.
_lock() {
    # NOTE: `exec 9>FILE` with no command makes redirections PERMANENT for the shell —
    # scope the stderr suppression to a brace group so it does not silence the rest of
    # the script (fd 9 still persists; brace groups don't subshell).
    { exec 9>"$LOCKFILE"; } 2>/dev/null || return 0   # lock is best-effort; never block work on it
    flock -w 30 9 2>/dev/null || log "WARN: could not acquire lock within 30s; proceeding"
}

# Job lines only (strip comments + blank lines), sorted unique, for set comparison.
# awk (not `grep -vE`) so ZERO matches still exit 0 — under `set -o pipefail` a grep
# that matches nothing (exit 1) would abort the guard below; awk also avoids the
# non-portable `\s`.
_jobs() { awk '!/^[[:space:]]*(#|$)/' | sort -u; }
canonical_jobs() { _jobs < "$CANONICAL"; }

# Guard: a truncated/empty/comment-only canonical must NEVER be installed as an empty
# crontab. Fail before any mode can act on it.
_CANON_N="$(canonical_jobs | wc -l | tr -d ' ')"
[ "${_CANON_N:-0}" -ge 1 ] || die "canonical has no job lines: $CANONICAL (refusing to act on an empty set)"

# Read the live crontab into a variable ONCE, tolerating "no crontab" (exit!=0).
# Sets READ_OK=1 on a clean read, 0 if `crontab -l` failed (wiped OR error).
read_live() {
    if LIVE_RAW="$("$CRONTAB_BIN" -l 2>/dev/null)"; then READ_OK=1; else LIVE_RAW=""; READ_OK=0; fi
}
live_jobs() { printf '%s\n' "$LIVE_RAW" | _jobs; }

backup_live() {
    mkdir -p "$BACKUP_DIR"
    # PID + second granularity so rapid successive mutations don't clobber each other.
    local dest="$BACKUP_DIR/crontab.$(date '+%Y%m%d_%H%M%S').$$.bak"
    printf '%s\n' "$LIVE_RAW" > "$dest"
    if [ "$READ_OK" = "1" ]; then
        log "backed up live crontab -> $dest ($(printf '%s\n' "$LIVE_RAW" | grep -vcE '^\s*#|^\s*$' 2>/dev/null || echo 0) jobs)"
    else
        log "WARN: could not read a live crontab (wiped or error) -> wrote $dest (may be empty)"
    fi
    echo "$dest"
}

case "$mode" in
  --print)
    canonical_jobs
    ;;

  --check)
    read_live
    if [ "$READ_OK" != "1" ]; then
        # Empty crontab is legitimate drift; distinguish a hard read error only when a
        # crontab is expected. We cannot tell them apart reliably, so treat a failed read
        # as drift (all-missing) — never as clean. Exit 1, not 0.
        echo "ensure_all_cron: DRIFT — no readable live crontab (wiped or unreadable); all canonical jobs missing"
        exit 1
    fi
    missing="$(comm -23 <(canonical_jobs) <(live_jobs))"
    extra="$(comm -13 <(canonical_jobs) <(live_jobs))"
    if [ -z "$missing" ] && [ -z "$extra" ]; then
        echo "ensure_all_cron: OK — live crontab matches canonical ($(canonical_jobs | wc -l | tr -d ' ') jobs)"
        exit 0
    fi
    echo "ensure_all_cron: DRIFT vs canonical:"
    [ -n "$missing" ] && { echo "  MISSING (in canonical, not live):"; printf '%s\n' "$missing" | sed 's/^/    - /'; }
    [ -n "$extra" ]   && { echo "  EXTRA (in live, not canonical):";   printf '%s\n' "$extra"   | sed 's/^/    + /'; }
    exit 1
    ;;

  --heal)
    _lock
    read_live
    if [ "$READ_OK" != "1" ]; then
        # Cannot read the crontab (wiped OR a real error). --heal's contract is additive
        # ("add missing, touch nothing else"); it CANNOT honor that blind, and rebuilding
        # from an empty read would drop any out-of-band lines. Refuse and point to --install
        # (the deliberate wholesale reprovision, safe from a wipe).
        log "heal: crontab unreadable — refusing (run --install to reprovision)"
        echo "ensure_all_cron: cannot read live crontab — run 'ensure_all_cron.sh --install' to reprovision from a wipe" >&2
        exit 3
    fi
    missing="$(comm -23 <(canonical_jobs) <(live_jobs))"
    if [ -z "$missing" ]; then
        log "heal: no missing canonical lines — no-op"
        echo "ensure_all_cron: nothing to heal"
        exit 0
    fi
    backup_live >/dev/null
    n="$(printf '%s\n' "$missing" | grep -c . || true)"
    # Build the new crontab from captured content (pipefail-safe; works from empty).
    # Keep existing non-blank lines, then append the missing canonical lines. Never
    # removes an out-of-band extra line.
    new="$(printf '%s\n' "$LIVE_RAW" | grep -vE '^\s*$' || true)"
    { [ -n "$new" ] && printf '%s\n' "$new"; printf '%s\n' "$missing"; } | "$CRONTAB_BIN" -
    log "heal: appended $n missing canonical line(s) (read_ok=$READ_OK)"
    echo "ensure_all_cron: healed — appended $n missing line(s)"
    ;;

  --install|"")
    _lock
    read_live
    backup_live >/dev/null
    # Wholesale install FROM THE COMMITTED FILE (never from the live read), so a
    # transient/empty live read can never cause an empty install.
    "$CRONTAB_BIN" - < "$CANONICAL"
    read_live   # re-read to verify
    [ "$READ_OK" = "1" ] || die "post-install verify FAILED: crontab not readable back"
    # Verify by CONTENT (set-equality), not just a job count.
    diff_out="$(comm -3 <(canonical_jobs) <(live_jobs))"
    if [ -n "$diff_out" ]; then
        die "post-install verify FAILED: live crontab != canonical set"
    fi
    installed="$(live_jobs | wc -l | tr -d ' ')"
    log "install: wrote canonical crontab ($installed jobs live; content-verified == canonical)"
    echo "ensure_all_cron: installed canonical crontab ($installed jobs)"
    ;;

  *)
    die "unknown mode '$mode' (use --install | --check | --heal | --print)"
    ;;
esac
