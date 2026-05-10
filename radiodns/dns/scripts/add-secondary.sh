#!/usr/bin/env bash
# Register a secondary (ns1-ns11) with the hidden master zone via PowerDNS API.
# Adds the secondary IP to ALSO-NOTIFY so the master notifies it on zone changes.
# Prints the pdnsutil commands to run on the secondary to complete the setup.
#
# Usage:
#   add-secondary.sh <secondary_ip> <ns_hostname>
#   Example: add-secondary.sh 203.0.113.10 ns1.zerotrustradio.org
#
# Environment:
#   PDNS_API_URL, PDNS_API_KEY
#   TSIG_KEY_NAME, TSIG_ALGO, TSIG_SECRET  (from setup-tsig.sh)
#   HIDDEN_MASTER_IP

set -euo pipefail

SECONDARY_IP="${1:?secondary IP required}"
NS_HOST="${2:?ns hostname required}"
API="${PDNS_API_URL:-http://127.0.0.1:8081}/api/v1/servers/localhost"
KEY="${PDNS_API_KEY:?required}"
DOMAIN="zerotrustradio.org"
TSIG_NAME="${TSIG_KEY_NAME:-axfr-transfer-key}"
TSIG_ALGO="${TSIG_ALGO:-hmac-sha256}"
TSIG_SECRET="${TSIG_SECRET:?TSIG_SECRET required}"
MASTER_IP="${HIDDEN_MASTER_IP:?HIDDEN_MASTER_IP required}"

api() {
  curl -sf -H "X-API-Key: ${KEY}" -H "Content-Type: application/json" "$@"
}

echo "==> Registering secondary ${NS_HOST} (${SECONDARY_IP})"

# Append to ALSO-NOTIFY (preserving existing values)
EXISTING=$(api "${API}/zones/${DOMAIN}./metadata/ALSO-NOTIFY" \
  | jq -r '.metadata[]' 2>/dev/null || true)
NEW_LIST=$(printf '%s\n%s\n' "${EXISTING}" "${SECONDARY_IP}" \
  | grep -v '^$' | sort -u | jq -R . | jq -s .)

api -X PUT "${API}/zones/${DOMAIN}./metadata/ALSO-NOTIFY" \
  -d "{\"metadata\":$(echo "${NEW_LIST}" | jq -c '.')}"

echo "    Added ${SECONDARY_IP} to ALSO-NOTIFY"
echo ""
echo "==> Run on ${NS_HOST}:"
cat <<CMDS

  # 1. Import the TSIG key
  docker exec pdns-secondary pdnsutil import-tsig-key \\
    ${TSIG_NAME} ${TSIG_ALGO} '${TSIG_SECRET}'

  # 2. Activate TSIG for incoming transfers
  docker exec pdns-secondary pdnsutil activate-tsig-key \\
    ${DOMAIN} ${TSIG_NAME} slave

  # 3. Register zone as secondary pointing to hidden master
  docker exec pdns-secondary pdnsutil create-slave-zone \\
    ${DOMAIN} ${MASTER_IP}

  # 4. Trigger initial AXFR
  docker exec pdns-secondary pdns_control retrieve ${DOMAIN}

  # 5. Verify
  dig @127.0.0.1 ${DOMAIN} SOA +short
  dig @127.0.0.1 _radiospi._tcp.${DOMAIN} SRV +short
  dig +dnssec @127.0.0.1 ${DOMAIN} SOA | grep -c RRSIG

CMDS
