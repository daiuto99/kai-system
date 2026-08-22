# buzz-relay — provenance & rebuild recipe (KAI-1157 [MR2])

The live `buzz-relay` container was hand-run (`docker run`) from the unversioned
`~/buzz-eval` clone — an ORPHAN relative to the kai-system tree. This file folds
its build provenance INTO the versioned tree so `~/buzz-eval` can be retired.

## What the image is
- **Image:** `buzz-relay:eval` (also tagged `buzz-relay:b1b283c-upstream`)
- **Image ID:** `5d4fc06b5d45`  ·  built 2026-07-31
- **Source:** STOCK upstream https://github.com/block/buzz (Apache-2.0)
- **Upstream commit built from:** `b1b283cd4c7f926e12eeee8ae1f38c7471922b16`
- **Local delta over upstream:** deploy-wiring ONLY — `docker-compose.yml`,
  `hostproxy.conf`, `rebind_secure.sh`, `.gitignore` (local commit `de11672`).
  **NOTHING in the Dockerfile or `crates/` is modified** → the image is a pure
  upstream build. The deploy-wiring delta is superseded by the kai-system
  compose service below, so it does not need to be carried.

## Preserved artifact (rebuild-proof)
- `backups/buzz-relay/buzz-relay-b1b283c-upstream.tar.gz` (gitignored, worker SSOT)
- sha256: `4af4aab81eae96d2854ff9a0edf98b5257133eb6951ddbd03160843904698476`
- Restore:  `gunzip -c backups/buzz-relay/buzz-relay-b1b283c-upstream.tar.gz | docker load`

## Rebuild from source (if ever needed)
    git clone https://github.com/block/buzz && cd buzz
    git checkout b1b283cd4c7f926e12eeee8ae1f38c7471922b16
    docker build -t buzz-relay:eval .

## DEFERRED cutover (follow-up ticket) — DATA-LOSS TRAP
The running container has **no volume** (`Mounts=[]`); its datastore lives in the
container writable layer (`/var/lib/buzz/repos` + event DB). Adopting it under
compose REQUIRES migrating that datastore to a named volume FIRST, in a
maintenance window, or relay state is wiped. Ready-to-apply service block:
see `buzz-relay/compose-service.yml` (NOT yet added to docker-compose.yml).
