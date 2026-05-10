#!/usr/bin/env bash
# Enable DNSSEC on zerotrustradio.org using pdnsutil.
# Run on the hidden master host or via docker exec.
#
# Usage:
#   ./enable-dnssec.sh
#   docker exec -it pdns-master bash -c 'DOMAIN=zerotrustradio.org /enable-dnssec.sh'

set -euo pipefail

DOMAIN="${DOMAIN:-zerotrustradio.org}"

echo "==> Securing zone ${DOMAIN} with DNSSEC (ECDSA P-256)"
pdnsutil secure-zone "${DOMAIN}"

echo "==> Setting NSEC3 opt-out (narrow mode)"
pdnsutil set-nsec3 "${DOMAIN}" '1 0 0 -' narrow

echo "==> Checking zone integrity"
pdnsutil check-zone "${DOMAIN}"

echo "==> Rectifying zone (builds NSEC3 ordering chain)"
pdnsutil rectify-zone "${DOMAIN}"

echo ""
echo "==> Key material:"
pdnsutil show-zone "${DOMAIN}"

echo ""
echo "==> DS records to publish at the registrar:"
pdnsutil export-zone-ds "${DOMAIN}"

echo ""
echo "==> Verification:"
echo "    dig +dnssec @127.0.0.1 ${DOMAIN} SOA"
echo "    dig +dnssec @127.0.0.1 ${DOMAIN} DNSKEY"
echo "    dig +dnssec @127.0.0.1 ${DOMAIN} NS"
