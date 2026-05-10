#!/usr/bin/env bash
# Generate a TSIG key on the hidden master and configure the zone to require
# TSIG for all AXFR/IXFR transfers to secondaries.
#
# Environment:
#   PDNS_API_URL   — default http://127.0.0.1:8081
#   PDNS_API_KEY
#   TSIG_KEY_NAME  — default axfr-transfer-key

set -euo pipefail

API="${PDNS_API_URL:-http://127.0.0.1:8081}/api/v1/servers/localhost"
KEY="${PDNS_API_KEY:?PDNS_API_KEY required}"
DOMAIN="zerotrustradio.org"
TSIG_NAME="${TSIG_KEY_NAME:-axfr-transfer-key}"
TSIG_ALGO="hmac-sha256"

api() {
  curl -sf -H "X-API-Key: ${KEY}" -H "Content-Type: application/json" "$@"
}

echo "==> Creating TSIG key '${TSIG_NAME}' (${TSIG_ALGO})"

TSIG_RESPONSE=$(api -X POST "${API}/tsigkeys" -d @- <<EOF
{"name": "${TSIG_NAME}", "algorithm": "${TSIG_ALGO}"}
EOF
)

TSIG_SECRET=$(echo "${TSIG_RESPONSE}" | jq -r '.key')

echo "    Key created."
echo ""
echo "    !! Store this secret securely — it is not recoverable from the API !!"
echo "    TSIG_SECRET=${TSIG_SECRET}"
echo ""

echo "==> Configuring zone ${DOMAIN} to require TSIG for AXFR"

for meta in "TSIG-ALLOW-AXFR" "AXFR-MASTER-TSIG"; do
  api -X PUT "${API}/zones/${DOMAIN}./metadata/${meta}" \
    -d "{\"metadata\": [\"${TSIG_NAME}\"]}" > /dev/null
  echo "    Set ${meta} = ${TSIG_NAME}"
done

echo ""
echo "==> TSIG configured. Distribute to each secondary with:"
echo "    pdnsutil import-tsig-key ${TSIG_NAME} ${TSIG_ALGO} '${TSIG_SECRET}'"
echo "    pdnsutil activate-tsig-key ${DOMAIN} ${TSIG_NAME} slave"
echo ""
echo "    Or use add-secondary.sh which includes these commands."
echo "    Export for use by add-secondary.sh:"
echo "    export TSIG_KEY_NAME=${TSIG_NAME} TSIG_ALGO=${TSIG_ALGO} TSIG_SECRET='${TSIG_SECRET}'"
