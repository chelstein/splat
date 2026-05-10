#!/usr/bin/env bash
# Publish a station logo to the logos.zerotrustradio.org CDN bucket.
# Filename is the SHA-256 hash of the file content — immutable, cache-forever.
#
# Usage:
#   publish-logos.sh <station_id> <image.png|image.jpg>
#
# Object path:
#   stations/<station_id>/logos/<sha256>.<ext>
#
# Environment:
#   AWS_ACCESS_KEY_ID     — Spaces access key
#   AWS_SECRET_ACCESS_KEY — Spaces secret key
#   SPACES_REGION         — default nyc3

set -euo pipefail

STATION_ID="${1:?station_id required}"
FILE="${2:?path to logo image required}"

BUCKET="ztr-logos"
REGION="${SPACES_REGION:-nyc3}"
ENDPOINT="https://${REGION}.digitaloceanspaces.com"

[[ -f "$FILE" ]] || { echo "error: not found: $FILE" >&2; exit 1; }

EXT="${FILE##*.}"
HASH=$(sha256sum "$FILE" | awk '{print $1}')
OBJECT_KEY="stations/${STATION_ID}/logos/${HASH}.${EXT}"

case "$EXT" in
  png)  CONTENT_TYPE="image/png"  ;;
  jpg|jpeg) CONTENT_TYPE="image/jpeg" ;;
  svg)  CONTENT_TYPE="image/svg+xml" ;;
  *)    CONTENT_TYPE="application/octet-stream" ;;
esac

# immutable — hash in filename guarantees content never changes at this URL
aws s3 cp "$FILE" "s3://${BUCKET}/${OBJECT_KEY}" \
  --endpoint-url "$ENDPOINT" \
  --content-type "$CONTENT_TYPE" \
  --cache-control "public, max-age=604800, immutable" \
  --acl public-read

echo "published: https://logos.zerotrustradio.org/${OBJECT_KEY}"
echo "hash:      ${HASH}"
