#!/bin/bash
# Backup integrity verify — S1-B3 (audit #01: "no restore has ever been tested").
# READ-ONLY: gzip/tar integrity + sqlite PRAGMA integrity_check via a throwaway
# container. Mutates nothing. Exit 0 = PASS, 1 = FAIL. Intended for a weekly cron
# + the green baseline. Full restore-into-scratch-DB is the stronger follow-up.
# NOTE: no `pipefail` — `grep -q`/`grep -m1` early-exit sends SIGPIPE to the
# upstream gunzip/tar, which pipefail would misread as corruption. gzip -t and
# tar tf fully test integrity on their own; the grep only checks content.
set -u
BK="${1:-$HOME/backups}"
fail=0

for store in plane buzz; do
    f=$(ls -1t "$BK/$store/"*.sql.gz 2>/dev/null | head -1)
    if [ -z "$f" ]; then echo "FAIL $store: no artifact"; fail=1; continue; fi
    if gzip -t "$f" 2>/dev/null && gunzip -c "$f" 2>/dev/null | grep -qE "CREATE TABLE|COPY |PostgreSQL database dump"; then
        echo "OK   $store: $(basename "$f") — gzip + SQL valid"
    else
        echo "FAIL $store: $(basename "$f") — corrupt or empty"; fail=1
    fi
done

f=$(ls -1t "$BK/n8n/"*.tar.gz 2>/dev/null | head -1)
if [ -n "$f" ] && tar tzf "$f" >/dev/null 2>&1; then
    tmp=$(mktemp -d)
    tar xzf "$f" -C "$tmp" 2>/dev/null
    res=$(docker run --rm -v "$tmp":/d:ro alpine sh -c "apk add -q sqlite >/dev/null 2>&1 && sqlite3 /d/database.sqlite 'PRAGMA integrity_check;'" 2>/dev/null | tail -1)
    if [ "$res" = "ok" ]; then echo "OK   n8n: $(basename "$f") — tar + sqlite integrity ok"; else echo "FAIL n8n: integrity=$res"; fail=1; fi
    rm -rf "$tmp"
else
    echo "FAIL n8n: no or invalid artifact"; fail=1
fi

f=$(ls -1t "$BK/qdrant/"*.snapshot 2>/dev/null | head -1)
if [ -n "$f" ] && tar tf "$f" 2>/dev/null | grep -qm1 "\.snapshot"; then
    echo "OK   qdrant: $(basename "$f") — tar valid, holds collection snapshots"
else
    echo "FAIL qdrant: no or invalid artifact"; fail=1
fi

echo "---"
STAMP="$BK/.verify_result"
NOW=$(date +%Y-%m-%dT%H:%M:%S%z)
if [ "$fail" -eq 0 ]; then
    echo "PASS $NOW" > "$STAMP"
    echo "BACKUP VERIFY: PASS"
else
    echo "FAIL $NOW" > "$STAMP"
    echo "BACKUP VERIFY: FAIL"
    exit 1
fi
