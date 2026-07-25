#!/usr/bin/env bash
# KAI-959 Gate 0 — fail-closed check that the Hermes hardened default profile is
# in force. READ-ONLY. Exit 0 = GREEN, exit 1 = RED (names the first failing
# invariant). Mirrors the KAI-958 green-baseline pattern. Portable: the mini
# ticket (864c4804) runs this unchanged once the mini is provisioned.
set -uo pipefail
CONFIG=${HERMES_CONFIG:-/home/leo/.hermes/config.yaml}
NET=${HERMES_EMBER_NET:-hermes-ember}
PY=${HERMES_PY:-/home/leo/.hermes/hermes-agent/venv/bin/python}
red(){ echo "GATE0 RED — $1"; exit 1; }

# 1. Config posture — the locked hardened default profile. Fully validated:
#    backend, docker_network, exactly-one --network hermes-ember (no contradictory
#    trailing flag), --read-only, non-root. (Findings #2/#4, Codex verify 2026-07-25.)
"$PY" - "$CONFIG" <<'PY' || red "config posture (backend/docker_network/run_as_host_user/--read-only/single --network hermes-ember)"
import sys, yaml
t = (yaml.safe_load(open(sys.argv[1])) or {}).get("terminal", {})
ex = t.get("docker_extra_args", []) or []
assert t.get("backend") == "docker", "terminal.backend != docker"
assert t.get("docker_network") is True, "terminal.docker_network is not true"
assert t.get("docker_run_as_host_user") is True, "docker_run_as_host_user is not true"
assert "--read-only" in ex, "docker_extra_args missing --read-only"
assert ex.count("--network") == 1, "docker_extra_args must contain exactly one --network"
assert ex[ex.index("--network") + 1] == "hermes-ember", "--network value is not hermes-ember"
PY

# 2. Egress network posture — internal net; ONLY kai-litellm + hermes sandboxes attached
docker network inspect "$NET" >/dev/null 2>&1 || red "network $NET missing"
[ "$(docker network inspect "$NET" --format '{{.Internal}}')" = "true" ] || red "$NET is not internal"
docker network inspect "$NET" --format '{{range .Containers}}{{.Name}} {{end}}' \
  | grep -qw kai-litellm || red "kai-litellm not attached to $NET"
for cname in $(docker network inspect "$NET" --format '{{range .Containers}}{{.Name}} {{end}}'); do
  [ "$cname" = "kai-litellm" ] && continue
  [ "$(docker inspect -f '{{index .Config.Labels "hermes-agent"}}' "$cname" 2>/dev/null)" = "1" ] \
    || red "$NET has unexpected (non-Ember, non-hermes) attachment: $cname"
done

# 3. Live sandbox posture — every hermes-agent=1 container (running OR stopped, since
#    Hermes reuse matches labels across all states — finding #1) must conform.
for c in $(docker ps -aq --filter label=hermes-agent=1); do
  n=$(docker inspect -f '{{.Name}}' "$c" | sed 's#^/##')
  [ "$(docker inspect -f '{{.Config.User}}' "$c")" = "1000:1000" ] || red "$n: user != 1000:1000 (root)"
  [ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$c")" = "true" ] || red "$n: rootfs not read-only"
  docker inspect -f '{{json .HostConfig.CapDrop}}' "$c" | grep -q '"ALL"' || red "$n: cap-drop ALL missing"
  docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$c" | grep -q no-new-privileges || red "$n: no-new-privileges missing"
  nets=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" | xargs)
  [ "$nets" = "$NET" ] || red "$n: networks != {$NET} (got: $nets)"
done

echo "GATE0 GREEN — Hermes hardened default profile in force (config + $NET + $(docker ps -aq --filter label=hermes-agent=1 | wc -l | xargs) labeled sandbox(es), all conforming)"
