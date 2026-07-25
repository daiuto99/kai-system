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

# 1. Config posture — the locked hardened default profile
"$PY" - "$CONFIG" <<'PY' || red "config posture (docker_run_as_host_user / --read-only / --network hermes-ember)"
import sys, yaml
t = (yaml.safe_load(open(sys.argv[1])) or {}).get("terminal", {})
ex = t.get("docker_extra_args", []) or []
assert t.get("docker_run_as_host_user") is True
assert "--read-only" in ex
assert "--network" in ex and "hermes-ember" in ex
PY

# 2. Egress network posture — internal net, only the Ember gateway attached
docker network inspect "$NET" >/dev/null 2>&1 || red "network $NET missing"
[ "$(docker network inspect "$NET" --format '{{.Internal}}')" = "true" ] || red "$NET is not internal"
docker network inspect "$NET" --format '{{range .Containers}}{{.Name}} {{end}}' \
  | grep -qw kai-litellm || red "kai-litellm not attached to $NET"

# 3. Live sandbox posture — every running hermes-agent=1 container must conform
for c in $(docker ps -q --filter label=hermes-agent=1); do
  n=$(docker inspect -f '{{.Name}}' "$c" | sed 's#^/##')
  [ "$(docker inspect -f '{{.Config.User}}' "$c")" = "1000:1000" ] || red "$n: user != 1000:1000 (root)"
  [ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$c")" = "true" ] || red "$n: rootfs not read-only"
  docker inspect -f '{{json .HostConfig.CapDrop}}' "$c" | grep -q '"ALL"' || red "$n: cap-drop ALL missing"
  docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$c" | grep -q no-new-privileges || red "$n: no-new-privileges missing"
  nets=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" | xargs)
  [ "$nets" = "$NET" ] || red "$n: networks != {$NET} (got: $nets)"
done

echo "GATE0 GREEN — Hermes hardened default profile in force (config + $NET + $(docker ps -q --filter label=hermes-agent=1 | wc -l | xargs) live sandbox(es))"
