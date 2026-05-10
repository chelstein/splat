#!/usr/bin/env bash
# Flush the DO CDN cache for a specific endpoint.
# Required for canonical SI.xml after a live update (TTL=60 but purge is instant).
#
# Usage:
#   invalidate-cdn.sh <cdn_uuid> [path ...]    # purge specific paths
#   invalidate-cdn.sh <cdn_uuid>               # purge entire CDN
#
# Find CDN UUIDs: doctl compute cdn list
#
# Environment:
#   DO_TOKEN — DigitalOcean API token

set -euo pipefail

CDN_ID="${1:?cdn_id (UUID) required}"
shift

: "${DO_TOKEN:?DO_TOKEN environment variable required}"

API="https://api.digitalocean.com/v2/cdn/endpoints/${CDN_ID}/cache"

if [[ $# -gt 0 ]]; then
  FILES_JSON=$(printf '%s\n' "$@" | jq -R . | jq -s .)
  PAYLOAD="{\"files\":${FILES_JSON}}"
else
  PAYLOAD='{"files":["*"]}'
fi

curl -sf -X DELETE \
  -H "Authorization: Bearer ${DO_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$API"

echo "CDN cache invalidated for endpoint ${CDN_ID}"
