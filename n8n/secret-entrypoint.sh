#!/bin/sh
set -eu

N8N_BASIC_AUTH_PASSWORD=$(tr -d '\n' < /run/secrets/n8n_basic_auth_password)
N8N_ENCRYPTION_KEY=$(tr -d '\n' < /run/secrets/n8n_encryption_key)
export N8N_BASIC_AUTH_PASSWORD N8N_ENCRYPTION_KEY

node /opt/kai/scrub-settings.cjs
exec /docker-entrypoint.sh "$@"
