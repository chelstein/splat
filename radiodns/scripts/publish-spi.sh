#!/usr/bin/env bash
# Publish SPI XML (SI.xml) to the spi.zerotrustradio.org CDN bucket.
#
# Usage:
#   publish-spi.sh <station_id> live      <file.xml>   # canonical SI.xml  TTL=60
#   publish-spi.sh <station_id> <version> <file.xml>   # versioned snapshot TTL=3600
#
# Object paths:
#   live:      stations/<id>/spi/SI.xml
#   versioned: stations/<id>/spi/<version>/SI.xml
#
# Environment:
#   AWS_ACCESS_KEY_ID     — Spaces access key
#   AWS_SECRET_ACCESS_KEY — Spaces secret key
#   SPACES_REGION         — default nyc3

set -euo pipefail

STATION_ID="${1:?station_id required}"
VERSION="${2:?version or 'live' required}"
FILE="${3:?path to SI.xml required}"

BUCKET="ztr-spi"
REGION="${SPACES_REGION:-nyc3}"
ENDPOINT="https://${REGION}.digitaloceanspaces.com"

[[ -f "$FILE" ]] || { echo "error: not found: $FILE" >&2; exit 1; }

if [[ "$VERSION" == "live" ]]; then
  OBJECT_KEY="stations/${STATION_ID}/spi/SI.xml"
  CACHE_CONTROL="public, max-age=60, s-maxage=600"
else
  OBJECT_KEY="stations/${STATION_ID}/spi/${VERSION}/SI.xml"
  CACHE_CONTROL="public, max-age=3600"
fi

aws s3 cp "$FILE" "s3://${BUCKET}/${OBJECT_KEY}" \
  --endpoint-url "$ENDPOINT" \
  --content-type "application/xml" \
  --cache-control "$CACHE_CONTROL" \
  --acl public-read

echo "published: https://spi.zerotrustradio.org/${OBJECT_KEY}"
