#!/bin/sh
# KAI-1479 · Buzz MinIO bucket provisioning-as-code (baked into the container, NO sidecar).
#
# On EVERY start — including a fresh/empty volume — this guarantees the required buckets
# exist, so object storage self-populates at t=0 and the Sep-12 "empty volume, dead bucket"
# class cannot recur silently. This is deliberately NOT a compose init sidecar: buzz_shim
# and the KAI-1478 watchdog both document that a sidecar in this stack is orphan-prone
# (a compose sweep orphans it and re-creates an outage). Instead the official image
# entrypoint runs as PID 1 (via exec) so signal handling + image setup are preserved,
# while a short-lived background waiter provisions buckets the moment the server answers.
#
# MinIO root creds are read from the container's OWN env (MINIO_ROOT_USER/PASSWORD) and
# never cross to the host or into process args (L18). Idempotent: `mc mb -p`.
set -eu

BUCKETS="${BUZZ_REQUIRED_BUCKETS:-buzz-media}"

provision() {
  i=0
  while [ "$i" -lt 60 ]; do
    if mc alias set loc "http://127.0.0.1:9000" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; then
      for b in $BUCKETS; do
        mc mb -p "loc/$b" >/dev/null 2>&1 || true
      done
      echo "[buzz-minio-provision] ensured buckets: $BUCKETS"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "[buzz-minio-provision] WARN: MinIO not ready after 60s; buckets not provisioned this start" >&2
  return 0
}

provision &
exec /usr/bin/docker-entrypoint.sh "$@"
