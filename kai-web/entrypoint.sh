#!/bin/sh
set -e
PASSWORD=$(cat /run/secrets/kai_web_password)
htpasswd -cb /etc/nginx/.htpasswd kai "$PASSWORD"

# The /api/ proxy forwards to kai-worker-api, which now authenticates every
# non-exempt route (Bug 48f85706/aec2d486). Attach the worker credential as a
# Basic-auth header on the proxied request — this dashboard proxy was
# previously bare and would 401/503 against every protected worker route.
WORKER_AUTH_B64=$(cat /run/secrets/kai_worker_auth | tr -d '\n' | base64 | tr -d '\n')
export WORKER_AUTH_B64
envsubst '${WORKER_AUTH_B64}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
