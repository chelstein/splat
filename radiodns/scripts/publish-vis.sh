#!/usr/bin/env bash
# Publish a RadioVIS slide image to the vis.zerotrustradio.org CDN bucket.
# Filename includes a Unix timestamp for cache-busting.
#
# Usage:
#   publish-vis.sh <station_id> <image.jpg>
#
# Object path:
#   stations/<station_id>/vis/<timestamp>.jpg
#
# Environment:
#   AWS_ACCESS_KEY_ID     — Spaces access key
#   AWS_SECRET_ACCESS_KEY — Spaces secret key
#   SPACES_REGION         — default nyc3

set -euo pipefail

STATION_ID="${1:?station_id required}"
FILE="${2:?path to VIS image required}"

BUCKET="ztr-vis"
REGION="${SPACES_REGION:-nyc3}"
ENDPOINT="https://${REGION}.digitaloceanspaces.com"

[[ -f "$FILE" ]] || { echo "error: not found: $FILE" >&2; exit 1; }

EXT="${FILE##*.}"
TS=$(date -u +%s)
OBJECT_KEY="stations/${STATION_ID}/vis/${TS}.${EXT}"

aws s3 cp "$FILE" "s3://${BUCKET}/${OBJECT_KEY}" \
  --endpoint-url "$ENDPOINT" \
  --content-type "image/jpeg" \
  --cache-control "public, max-age=3600" \
  --acl public-read

echo "published: https://vis.zerotrustradio.org/${OBJECT_KEY}"
