#!/usr/bin/env bash
# Substitute env var secrets into pdns.conf, then exec pdns_server.
# Runs as the Docker ENTRYPOINT for the hidden master container.
set -euo pipefail

: "${PDNS_DB_PASSWORD:?PDNS_DB_PASSWORD required}"
: "${PDNS_API_KEY:?PDNS_API_KEY required}"

sed \
  -e "s/PDNS_DB_PASSWORD_PLACEHOLDER/${PDNS_DB_PASSWORD}/" \
  -e "s/PDNS_API_KEY_PLACEHOLDER/${PDNS_API_KEY}/" \
  /etc/powerdns/pdns.conf.tpl > /etc/powerdns/pdns.conf

exec pdns_server --daemon=no --guardian=no --config-dir=/etc/powerdns "$@"
