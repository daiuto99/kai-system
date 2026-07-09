#!/bin/sh
set -e
PASSWORD=$(cat /run/secrets/kai_web_password)
htpasswd -cb /etc/nginx/.htpasswd kai "$PASSWORD"
exec nginx -g 'daemon off;'
