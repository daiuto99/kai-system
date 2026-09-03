#!/bin/bash
# Vault recovery snapshot (KAI N2, C2). The vault is RW-mounted into several
# containers with no undo; this commits any changes to the vault's local git repo
# so a bad container write is always recoverable. Local-only — never pushed (the
# vault holds KEYSTONE/personal data; the worker is the single source of truth).
#
# Wired via worker_crontab.canonical; installed by ensure_all_cron.sh.
set -uo pipefail

VAULT="/home/leo/vault"
LOCK="$VAULT/.git/.snapshot.lock"

cd "$VAULT" 2>/dev/null || { echo "vault_snapshot: $VAULT missing"; exit 1; }
[ -d "$VAULT/.git" ] || { echo "vault_snapshot: $VAULT is not a git repo"; exit 1; }

# Best-effort serialize against an overlapping run.
exec 9>"$LOCK" 2>/dev/null && flock -w 20 9 2>/dev/null

git add -A
if git diff --cached --quiet; then
    echo "[$(date -u +%Y-%m-%dT%H:%MZ)] vault_snapshot: no changes"
    exit 0
fi
git commit -q -m "vault snapshot $(date -u +%Y-%m-%dT%H:%MZ)" \
    && echo "[$(date -u +%Y-%m-%dT%H:%MZ)] vault_snapshot: committed $(git rev-parse --short HEAD)"
