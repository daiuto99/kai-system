# HostOps Fleet-Host Secret Placement — design (AR-2)

**Ticket:** a38408ec (reframed) — the authorized, gated path for KAI to place an
existing named secret onto a **fleet host** (starting with 71-kai-mini), so skills
like `daily_brief` get their keys without a human in the execution loop and without
dodging the mode-lock secrets guard.

**Why (§3 gate):** far proactive-queueing + behavioral floor. Realizes the
`authorized_execution_path` / KAI-984 principle: *KAI holds and moves secrets by
name, Leo taps to authorize, every move audited.* This is the fleet twin of the
Cloudways publish-gate rail.

**Retire/simplify (§5):** removes the manual hand-placement dead-end and the
"Leo scp's it himself" execution-loop. Reuses the EXISTING HostOps gate / policy /
audit / resolver spine — no new gate machinery, no second approval surface.

## What exists vs. what's new

REUSE (unchanged): council approval-gate, `check_policy`, audit binding /
reconciliation, `HostOpsSecretResolver` (0600 exec-time byte read, never persisted),
`InMemorySecret`, `CapabilityResult`, the gate→exec workflow scaffold.

NEW (this build):
1. **Fleet target type** — `HostOpsFleetTarget(host, ssh_user, ssh_key_path,
   dest_dir, uid)`, resolved from an allowlist `/vault/00_System/fleet_hosts.json`
   (NOT wordpress_sites.json). First entry = the mini:
   `{ "kai-mini": { "host": "100.85.243.2", "ssh_user": "leo",
      "ssh_key": "/run/secrets/kai_fleet_ssh_key", "dest_dir": "/home/leo/.hermes/secrets" } }`
   `audit_identity` = `fleet-host:kai-mini`.
2. **Fleet transport** — `FleetSshTransport.place_secret(target, secret_bytes,
   secret_name)`: bytes flow ONLY on stdin, never argv/log/result (L18):
   `ssh -i <key> <user>@<host> 'umask 077; install -m600 /dev/stdin <dest_dir>/<name>'`
   then a readback `test -f` + `stat -c %a` == 600 confirmation (never cats the value).
   Fixed argv, no caller-controlled command (mirrors the master-operator model).
3. **New capability** `hostops.place_fleet_secret` — registered in capability_map.json,
   gated via `gate_type=hostops_place_fleet_secret`. Same resolve-after-approval shape
   as `_step_place_secret`.
4. **Payload staging** — create `/run/hostops-payload-secrets/kai-mini/` (0700,
   runtime-owned) and stage `todoist_api_key` (0600) from the worker's existing
   `todoist_api_key` docker secret. This is the a38408ec "dir absent" fix, generalized.
5. **Orchestrator mini credential** — mount a mini SSH key into the orchestrator as
   `/run/secrets/kai_fleet_ssh_key` (compose secret). Least-privilege: a key that can
   only write under `~/.hermes/secrets` is ideal but out-of-scope v1; v1 uses the
   existing fleet key, gated per-placement.

## Gate brief (what Leo approves)
`{ hostops_operation: place_fleet_secret, target: kai-mini, secret_name: todoist_api_key,
   dest: ~/.hermes/secrets/todoist_api_key, required_decision: "place secret X on host Y" }`
— a reference only, never the bytes.

## Test plan (Codex verifies)
- Resolver rejects non-0600 / wrong-owner / traversal paths (existing tests cover this).
- Transport: bytes never appear in argv, logs, or CapabilityResult (grep the audit record).
- Gate required: no gate_id + policy!=allow ⇒ `gate_required`, no placement.
- Readback asserts mode 600 on the mini; wrong mode ⇒ failed_permanent.
- End-to-end (gated): place `todoist_api_key` on the mini; `daily_brief` shadow then
  pulls real Todoist and the 5-cycle parity runs.

## Sequence
1. This session: design (this doc) + build capability + target + transport + register + unit tests.
2. Stage payload + mount mini key (compose) → Codex verify → gated placement of the Todoist key.
3. Then AR-2 daily_brief: 5-cycle shadow parity with real Todoist → delivery-surface decision → cutover.
