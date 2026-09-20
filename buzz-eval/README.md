# buzz-eval — Buzz DATA tier (compose project `buzz`)

Canonical, version-controlled definition of the Buzz **data tier**. Tracked in the
kai-system repo (`github.com/daiuto99/kai-system`) at `buzz-eval/`. This exists because on
2026-09-12 (KAI-1475/KAI-1400) the original on-disk `~/buzz-eval` dir was deleted while the
containers kept running, and a MinIO volume was silently re-initialized empty — the
`buzz-media` bucket was gone for 8 days. Version control + external volumes + provisioning-
as-code (KAI-1479) make that failure class structurally impossible.

## The two-project split (read before touching either)

The Buzz stack spans **two** compose projects:

| Project      | File                                          | Owns                                            |
|--------------|-----------------------------------------------|-------------------------------------------------|
| `buzz`       | `buzz-eval/docker-compose.yml` (this dir)     | DATA tier: `buzz-minio`, `buzz-postgres`, `buzz-redis` |
| `kai-system` | `kai-system/docker-compose.yml`               | APP tier: `buzz-relay`, `kai-buzz`, `kai-buzz-shim`, `buzz-hostproxy` |

Service names and `container_name`s are **load-bearing** — the app resolves `postgres`,
`redis`, and `http://buzz-minio:9000` by DNS on the shared external `buzz-net`. Do not
rename. `buzz-net` and all three stateful volumes are declared `external: true` so a
`compose up`/`down` reuses the existing stores and never re-initializes onto a fresh volume.

## Source of truth / no drift

- **Canonical:** `/home/leo/kai-system/buzz-eval/` (this git-tracked dir).
- **Live runtime:** `/home/leo/buzz-eval/docker-compose.yml` is a **symlink** to the canonical
  file, so the runtime and the tracked copy cannot drift. `.env` (real secrets, chmod 600)
  lives only in the live dir and is gitignored; `.env.example` here documents its keys.
- Manage from either path — both resolve to this file.

## Bucket provisioning-as-code (KAI-1479)

`buzz-minio-provision.sh` is bind-mounted into `buzz-minio` and set as its entrypoint. It
execs the official image entrypoint as PID 1 (signals + setup preserved) and, in the
background, ensures every bucket in `$BUZZ_REQUIRED_BUCKETS` exists the moment the server is
ready — so a fresh/empty volume self-populates at t=0. This is intentionally **not** a
compose init sidecar (orphan-prone in this stack). It supersedes the KAI-1478 host-cron
bucket self-heal, which is kept until this proves out, then retired.

## Operate

```sh
# recreate just MinIO after a change (deliberate, one container — don't batch-bounce the stack)
docker compose -f /home/leo/buzz-eval/docker-compose.yml up -d minio
# verify the store
docker exec buzz-minio sh -c 'mc alias set loc http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc ls loc'
```
