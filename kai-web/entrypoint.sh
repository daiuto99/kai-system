#!/bin/sh
set -e
PASSWORD=$(cat /run/secrets/kai_web_password)
htpasswd -cb /etc/nginx/.htpasswd kai "$PASSWORD"
unset PASSWORD

# The /api/ proxy forwards to kai-worker-api, which now authenticates every
# non-exempt route (Bug 48f85706/aec2d486). Preserve an nginx-process-only
# environment value for njs. It comes from the secret mount and is never
# rendered into a config or copied into another file.
credential=$(tr -d '\n' < /run/secrets/kai_worker_auth)
KAI_WORKER_AUTH_B64=$(printf '%s' "$credential" | base64 | tr -d '\n')
export KAI_WORKER_AUTH_B64
unset credential

cp /etc/nginx/templates/default.conf.template /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
