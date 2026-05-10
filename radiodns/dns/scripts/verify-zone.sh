#!/usr/bin/env bash
# Full zone verification suite.
# Run against the hidden master (127.0.0.1) or a public secondary.
#
# Usage:
#   ./verify-zone.sh                              # test hidden master
#   NS_HOST=ns1.zerotrustradio.org ./verify-zone.sh  # test public secondary
#   MASTER_IP=10.0.0.1 ./verify-zone.sh           # test master by IP

set -euo pipefail

DOMAIN="zerotrustradio.org"
NS="${NS_HOST:-${MASTER_IP:-127.0.0.1}}"
PASS=0
FAIL=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf "  [PASS] %s\n" "${label}"
    ((PASS++)) || true
  else
    printf "  [FAIL] %s\n" "${label}"
    ((FAIL++)) || true
  fi
}

echo "=== pdnsutil zone checks (run on hidden master) ==="
check "pdnsutil check-zone"          pdnsutil check-zone "${DOMAIN}"
check "pdnsutil: DNSSEC keys exist"  bash -c "pdnsutil show-zone '${DOMAIN}' | grep -q KSK"

echo ""
echo "=== DNS resolution: ${NS} ==="
check "SOA resolves"                 dig @"${NS}" "${DOMAIN}" SOA +short
check "NS records present"           dig @"${NS}" "${DOMAIN}" NS  +short | grep -q 'ns1\.' 
check "DNSSEC: DNSKEY present"       dig @"${NS}" "${DOMAIN}" DNSKEY +short | grep -q 256
check "DNSSEC: SOA has RRSIG"        dig +dnssec @"${NS}" "${DOMAIN}" SOA  | grep -q RRSIG
check "DNSSEC: AD bit set"           dig +dnssec @"${NS}" "${DOMAIN}" SOA  | grep -q 'flags.*ad'
check "CNAME spi→CDN"                dig @"${NS}" "spi.${DOMAIN}"      CNAME +short | grep -q 'cdn.digitaloceanspaces.com'
check "CNAME logos→CDN"              dig @"${NS}" "logos.${DOMAIN}"    CNAME +short | grep -q 'cdn.digitaloceanspaces.com'
check "CNAME epg→CDN"                dig @"${NS}" "epg.${DOMAIN}"      CNAME +short | grep -q 'cdn.digitaloceanspaces.com'
check "CNAME vis→CDN"                dig @"${NS}" "vis.${DOMAIN}"      CNAME +short | grep -q 'cdn.digitaloceanspaces.com'
check "CNAME evidence→CDN"           dig @"${NS}" "evidence.${DOMAIN}" CNAME +short | grep -q 'cdn.digitaloceanspaces.com'
check "SRV _radiospi._tcp"           dig @"${NS}" "_radiospi._tcp.${DOMAIN}" SRV +short | grep -q 'spi\.'
check "SRV _radioepg._tcp"           dig @"${NS}" "_radioepg._tcp.${DOMAIN}" SRV +short | grep -q 'epg\.'
check "SRV _radiovis._tcp"           dig @"${NS}" "_radiovis._tcp.${DOMAIN}" SRV +short | grep -q 'vis\.'
check "SRV _radiotag._tcp"           dig @"${NS}" "_radiotag._tcp.${DOMAIN}" SRV +short | grep -q 'api\.'
check "TXT SPF"                      dig @"${NS}" "${DOMAIN}" TXT +short | grep -q 'v=spf1'
check "TXT _radiodns"                dig @"${NS}" "_radiodns.${DOMAIN}" TXT +short | grep -q 'v=RadioDNS1'

echo ""
echo "=== AXFR check (hidden master only) ==="
check "AXFR without TSIG is refused" \
  bash -c "! dig @127.0.0.1 ${DOMAIN} AXFR 2>&1 | grep -v 'Transfer failed'"

echo ""
echo "=== Security: API not exposed on public port ==="
check "Port 8081 is closed on ${NS}" \
  bash -c "! curl -sf --connect-timeout 3 http://${NS}:8081/api/v1/servers 2>/dev/null"

echo ""
if [[ ${FAIL} -eq 0 ]]; then
  echo "All ${PASS} checks passed."
else
  echo "${PASS} passed, ${FAIL} FAILED."
  exit 1
fi
