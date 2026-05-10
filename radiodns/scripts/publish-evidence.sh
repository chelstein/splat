#!/usr/bin/env bash
# Publish a validation artifact or metadata snapshot to evidence.zerotrustradio.org.
# Filename is the SHA-256 hash of the file content — immutable, auditable.
#
# Usage:
#   publish-evidence.sh <capture_id> <artifact.json>
#
# Object path:
#   evidence/<capture_id>/<sha256>.json
#
# Environment:
#   AWS_ACCESS_KEY_ID     — Spaces access key
#   AWS_SECRET_ACCESS_KEY — Spaces secret key
#   SPACES_REGION         — default nyc3

set -euo pipefail

CAPTURE_ID="${1:?capture_id required}"
FILE="${2:?path to artifact file required}"

BUCKET="ztr-evidence"
REGION="${SPACES_REGION:-nyc3}"
ENDPOINT="https://${REGION}.digitaloceanspaces.com"

[[ -f "$FILE" ]] || { echo "error: not found: $FILE" >&2; exit 1; }

EXT="${FILE##*.}"
HASH=$(sha256sum "$FILE" | awk '{print $1}')
OBJECT_KEY="evidence/${CAPTURE_ID}/${HASH}.${EXT}"

# immutable — hash filename is content-addressed; artifacts are write-once
aws s3 cp "$FILE" "s3://${BUCKET}/${OBJECT_KEY}" \
  --endpoint-url "$ENDPOINT" \
  --content-type "application/json" \
  --cache-control "public, max-age=86400, immutable" \
  --acl public-read

echo "published: https://evidence.zerotrustradio.org/${OBJECT_KEY}"
echo "hash:      ${HASH}"
