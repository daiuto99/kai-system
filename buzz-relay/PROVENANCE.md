# buzz-relay — provenance & rebuild recipe (KAI-1157 [MR2])

The live `buzz-relay` container was hand-run (`docker run`) from the unversioned
`~/buzz-eval` clone — an ORPHAN relative to the kai-system tree. This file folds
its build provenance INTO the versioned tree so `~/buzz-eval` can be retired.

## What the image is
- **Image:** `buzz-relay:b1b283c-upstream` (the orphan `:eval` tag was retired
  2026-08-22 at cutover; both tags were the same image id).
- **Image ID:** `5d4fc06b5d45`  ·  built 2026-07-31
- **Source:** STOCK upstream https://github.com/block/buzz (Apache-2.0)
- **Upstream commit built from:** `b1b283cd4c7f926e12eeee8ae1f38c7471922b16`
- **Local delta over upstream:** deploy-wiring ONLY — `docker-compose.yml`,
  `hostproxy.conf`, `rebind_secure.sh`, `.gitignore` (local commit `de11672`).
  **NOTHING in the Dockerfile or `crates/` is modified** → the image is a pure
  upstream build. The deploy-wiring delta is superseded by the kai-system
  compose service, so it does not need to be carried.

## Preserved artifact (rebuild-proof)
- `backups/buzz-relay/buzz-relay-b1b283c-upstream.tar.gz` (gitignored, worker SSOT)
- sha256: `4af4aab81eae96d2854ff9a0edf98b5257133eb6951ddbd03160843904698476`
- Restore:  `gunzip -c backups/buzz-relay/buzz-relay-b1b283c-upstream.tar.gz | docker load`

## Rebuild from source (if ever needed)
    git clone https://github.com/block/buzz && cd buzz
    git checkout b1b283cd4c7f926e12eeee8ae1f38c7471922b16
    docker build -t buzz-relay:b1b283c-upstream .

## Cutover — APPLIED 2026-08-22 (KAI-1157 [MR2])
The hand-run container was adopted into `docker-compose.yml` (service `buzz-relay`,
`restart: always`) so the PRIMARY-comms relay survives reboots + cleanup sweeps —
a hand-run service is exactly what caused the 11-day advisor-DM shim outage.

Key finding that de-risked the "data-loss trap": the relay is **stateless**.
`docker diff` showed the writable layer held only an ephemeral pack-cache; all
durable state lives in the sibling containers `buzz-postgres` (named volume
`buzz-postgres-data`), `buzz-redis`, and `buzz-minio` (S3 media). The relay
connects to them over `buzz-net` via the network aliases `postgres`/`redis`
(owned by those containers) and `buzz-minio` by name.

Steps taken:
1. Backed up `/var/lib/buzz` → `backups/buzz-relay/buzz-relay-vlb-<ts>.tar`.
2. Created + pre-populated named volume `kai-system_buzz-relay-data` from the
   running container's `/var/lib/buzz` (preserving `buzz`/uid-1000 ownership) so
   the non-root relay can write its cache.
3. `docker rm -f buzz-relay` (the hand-run container) → `docker compose up -d
   buzz-relay` on the versioned tag.
4. Verified: relay ingesting live traffic (kind:0 profiles synced to postgres,
   NIP-29 membership events), `restart=always`, on `buzz-net`, resolving
   `postgres`; retired the `:eval` tag.

### Env fidelity (why the earlier staged snippet was incomplete)
The relay needs its full runtime env or it comes up unable to reach postgres.
That env is faithfully reproduced from `docker inspect` in the **gitignored**
`kai-buzz-relay.env` (dev creds match the hand-run buzz stack). Documented here
so it is reproducible from a clean clone:

    PGHOST=postgres  PGPORT=5432  PGUSER=buzz  PGPASSWORD=<dev>  PGDATABASE=buzz
    DATABASE_URL=postgres://buzz:<dev>@postgres:5432/buzz
    REDIS_URL=redis://redis:6379
    BUZZ_S3_ENDPOINT=http://buzz-minio:9000  BUZZ_S3_ACCESS_KEY=buzz_dev
    BUZZ_S3_SECRET_KEY=<dev>  BUZZ_S3_REGION=us-east-1
    BUZZ_S3_ADDRESSING_STYLE=path  BUZZ_S3_BUCKET=buzz-media
    BUZZ_AUTO_MIGRATE=true  BUZZ_BIND_ADDR=0.0.0.0:3000
    RELAY_URL=wss://kai-worker.tail7f43c5.ts.net
    BUZZ_ADMIN_WEB_DIR=/srv/buzz/admin-web  BUZZ_WEB_DIR=/srv/buzz/web
    RUST_LOG=buzz_relay=info

The canonical service definition now lives in `docker-compose.yml` (service
`buzz-relay`); the previously-staged `compose-service.yml` was removed at cutover
to avoid a drifting duplicate.

## kai-buzz decouple — APPLIED 2026-08-22 (KAI-1157 [MR2])
The `kai-buzz` container previously bind-mounted `~/buzz-eval/agent` (inside the
orphan upstream clone) for its advisor Nostr **keys** + channel state. Verified
`/agent` is pure DATA — `websockets`/`coincurve`/`nostr-sdk` are pip-installed in
the image, and the code's `sys.path` `libs` resolves to `/app/libs`, not the mount.

Steps taken:
1. Backed up the irreplaceable advisor keys (root-readable via the container) to
   `~/buzz-agent-backup-<ts>.tar.gz` (in `$HOME`, deliberately OUTSIDE git — keys
   never enter the repo).
2. `mv ~/buzz-eval/agent ~/buzz-agent` — a dedicated, minimal state dir; then
   scrubbed vestigial spike code (old `*.py`, `libs/`, `__pycache__`, `watchdog.sh`,
   stale `*.log`). `~/buzz-agent` now holds ONLY keys + channel files + avatars +
   markers. Kept as an inspectable bind-mount (not a named volume) because these
   keys are the sole copies of the advisor identities.
3. Repointed the `kai-buzz` compose mount `~/buzz-eval/agent` → `~/buzz-agent`,
   recreated the container; verified all 8 bridges reconnect + online.
4. Retired the dead copies: `~/buzz-eval` deleted whole (took
   `_archived_kai1142/agents_bridge.py` with it; `kai_openai_shim.py`/`watchdog.sh`
   removed in the scrub), and `kai-worker-api/scheduler.py` deleted from the tree
   (dormant webhook; long-poll is the live transport; no importers, no Dockerfile
   COPY). Stripped the `~/buzz-eval/agent` default from the live bridge code
   (`agents_bridge.py`, `ember_bridge.py` → default `/agent`).

The `buzz_eval_dependency` orphan is CLOSED — `~/buzz-eval` no longer exists and
nothing live references it (remaining `"buzz-eval"` strings are identifiers/labels,
not paths).

### Carry (diagnostic only)
`scripts/mr2_path_inventory.py` still hard-codes the old `~/buzz-eval/agent` paths;
update the checker to assert the orphan is RESOLVED (points at `~/buzz-agent`) next
time it's revised. Not a runtime dependency.
