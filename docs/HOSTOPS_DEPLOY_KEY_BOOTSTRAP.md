# Host-ops deploy-key bootstrap

This is the one-time root-of-trust setup for KAI-820 HOSTOPS-(a). It creates one
Cloudways SSH identity per WordPress application. Do not place any private key,
passphrase, API token, or app password in this document, git, shell history, or
chat transcript.

## Layout and contract

The orchestrator runs as UID 1000 and receives a read-only mount:

```
/run/hostops-deploy-keys/
  cloudways-app-<cloudways_app_id>-<cloudways_sys_user>.ed25519
```

Each file is a single app's private deploy key, is mode `0600`, and is owned by
UID 1000. The filename is deterministic from the app ID and app user recorded in
`vault/00_System/wordpress_sites.json`; a key cannot be selected for another app.
`HostOpsIdentityResolver.resolve(site_key)` returns a `DeployKeyHandle`, not key
bytes. Only `DeployKeyLoader.with_material()` may read the file, immediately at
the future SSH transport boundary.

## Leo's one-time setup

Repeat the following for each application. Generate the keypair on Leo's secure
machine or directly in the worker secret-store directory; never paste its private
component into a terminal, ticket, or chat.

1. Generate an Ed25519 deploy key for exactly one Cloudways application.
2. Add its public component to that application's Cloudways SSH/deploy-key list.
   Do not add it to the server-wide master identity.
3. Place the private component at its deterministic filename in
   `/home/leo/kai-system/secrets/hostops-deploy-keys/`, owner UID `1000`, mode
   `0600`. The directory itself is mode `0700`.
4. Keep the file content out of command arguments. Copy from a file/standard input
   and use an atomic same-directory replacement when rotating.
5. Restart/recreate `kai-orchestrator` only after the read-only mount is present.

## Rotation and revocation

Rotation preserves the filename so no capability configuration changes: stage a
new private key in the same secret directory with restrictive permissions, atomically
rename it over `cloudways-app-<id>-<user>.ed25519`, then remove the old public key
from Cloudways after a successful read-only verification. Revocation removes the
Cloudways public key and the corresponding secret-store file together. No live key
is ever committed.

## Read-only verification for HOSTOPS-(b)

After bootstrap, the future autonomous `hostops.status` and `hostops.verify`
operations must verify only these facts:

1. `HostOpsIdentityResolver.resolve(site_key)` returns the expected app-specific
   handle and no key material.
2. The resolved file exists, is owner UID 1000, and is mode `0600`.
3. The matching deploy key can perform a read-only SSH identity/probe against its
   own Cloudways application; it must not mutate files or run a shell payload.

Any mismatch is a fail-closed status result with the app handle only. It must not
include private-key bytes, a key fingerprint derived from private data, or a secret
path outside the approved secret store.

## Deliberate boundary

This bootstrap provides identities only. It does not authorize operations:
HOSTOPS-(b) supplies the two named operations, HOSTOPS-(c) supplies council gates
for mutations, and HOSTOPS-(d) supplies approval-to-action audit reconciliation.
