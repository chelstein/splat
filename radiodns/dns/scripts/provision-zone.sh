#!/usr/bin/env bash
# Provision the zerotrustradio.org zone on the hidden master via PowerDNS API.
# Creates the zone, NS records, NS glue A records, CNAME records for CDN
# subdomains, SRV records for RadioDNS service discovery, and TXT records.
#
# Run after the hidden master container is healthy.
# Run setup-tsig.sh and enable-dnssec.sh afterward.
#
# Environment:
#   PDNS_API_URL   — e.g. http://127.0.0.1:8081  (default)
#   PDNS_API_KEY   — X-API-Key value
#   NS_IPS         — space-separated list of ns1-ns11 IP addresses
#                    e.g. "1.2.3.4 5.6.7.8 ..." (11 values)

set -euo pipefail

API="${PDNS_API_URL:-http://127.0.0.1:8081}/api/v1/servers/localhost"
KEY="${PDNS_API_KEY:?PDNS_API_KEY required}"
DOMAIN="zerotrustradio.org"
CDN="nyc3.cdn.digitaloceanspaces.com"

api() {
  curl -sf \
    -H "X-API-Key: ${KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

patch_zone() {
  api -X PATCH "${API}/zones/${DOMAIN}." -d "$1"
}

echo "==> Creating zone ${DOMAIN}"
api -X POST "${API}/zones" -d @- <<'EOF'
{
  "name": "zerotrustradio.org.",
  "kind": "Native",
  "nameservers": [
    "ns1.zerotrustradio.org.",  "ns2.zerotrustradio.org.",
    "ns3.zerotrustradio.org.",  "ns4.zerotrustradio.org.",
    "ns5.zerotrustradio.org.",  "ns6.zerotrustradio.org.",
    "ns7.zerotrustradio.org.",  "ns8.zerotrustradio.org.",
    "ns9.zerotrustradio.org.",  "ns10.zerotrustradio.org.",
    "ns11.zerotrustradio.org."
  ]
}
EOF

echo "==> Adding NS glue A records"
# Build rrsets JSON from NS_IPS env var
read -ra IPS <<< "${NS_IPS:-}"
if [[ ${#IPS[@]} -eq 11 ]]; then
  GLUE_RRSETS="["
  for i in "${!IPS[@]}"; do
    N=$((i + 1))
    GLUE_RRSETS+="{\"name\":\"ns${N}.${DOMAIN}.\",\"type\":\"A\",\"ttl\":3600,"
    GLUE_RRSETS+="\"changetype\":\"REPLACE\","
    GLUE_RRSETS+="\"records\":[{\"content\":\"${IPS[$i]}\",\"disabled\":false}]}"
    [[ $N -lt 11 ]] && GLUE_RRSETS+=","
  done
  GLUE_RRSETS+="]"
  patch_zone "{\"rrsets\":${GLUE_RRSETS}}"
else
  echo "  WARNING: NS_IPS not set or not 11 values — skipping glue records"
  echo "           Set manually with pdnsutil or the API after provisioning."
fi

echo "==> Adding CNAME records (CDN subdomains)"
patch_zone "$(cat <<EOF
{
  "rrsets": [
    {"name":"spi.${DOMAIN}.",     "type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"ztr-spi.${CDN}.",     "disabled":false}]},
    {"name":"logos.${DOMAIN}.",   "type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"ztr-logos.${CDN}.",   "disabled":false}]},
    {"name":"epg.${DOMAIN}.",     "type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"ztr-epg.${CDN}.",     "disabled":false}]},
    {"name":"vis.${DOMAIN}.",     "type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"ztr-vis.${CDN}.",     "disabled":false}]},
    {"name":"evidence.${DOMAIN}.","type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"ztr-evidence.${CDN}.","disabled":false}]},
    {"name":"api.${DOMAIN}.",     "type":"CNAME","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"${DOMAIN}.ondigitalocean.app.","disabled":false}]}
  ]
}
EOF
)"

echo "==> Adding SRV records (RadioDNS service discovery, RFC 5507)"
patch_zone "$(cat <<EOF
{
  "rrsets": [
    {"name":"_radiospi._tcp.${DOMAIN}.","type":"SRV","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"0 0 443 spi.${DOMAIN}.","disabled":false}]},
    {"name":"_radioepg._tcp.${DOMAIN}.","type":"SRV","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"0 0 443 epg.${DOMAIN}.","disabled":false}]},
    {"name":"_radiovis._tcp.${DOMAIN}.","type":"SRV","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"0 0 443 vis.${DOMAIN}.","disabled":false}]},
    {"name":"_radiotag._tcp.${DOMAIN}.","type":"SRV","ttl":300,"changetype":"REPLACE",
     "records":[{"content":"0 0 443 api.${DOMAIN}.","disabled":false}]}
  ]
}
EOF
)"

echo "==> Adding TXT records"
patch_zone "$(cat <<EOF
{
  "rrsets": [
    {"name":"${DOMAIN}.",          "type":"TXT","ttl":3600,"changetype":"REPLACE",
     "records":[{"content":"\\"v=spf1 include:digitalocean.com ~all\\"","disabled":false}]},
    {"name":"_radiodns.${DOMAIN}.","type":"TXT","ttl":3600,"changetype":"REPLACE",
     "records":[{"content":"\\"v=RadioDNS1 auth=zerotrustradio\\"","disabled":false}]},
    {"name":"_dmarc.${DOMAIN}.",   "type":"TXT","ttl":3600,"changetype":"REPLACE",
     "records":[{"content":"\\"v=DMARC1; p=quarantine; rua=mailto:dmarc@${DOMAIN}\\"","disabled":false}]}
  ]
}
EOF
)"

echo ""
echo "==> Zone ${DOMAIN} provisioned."
echo "    Next steps:"
echo "    1. radiodns/dns/scripts/setup-tsig.sh"
echo "    2. radiodns/dns/scripts/enable-dnssec.sh"
echo "    3. radiodns/dns/scripts/add-secondary.sh <ip> <ns-hostname>  (x11)"
echo "    4. Set glue records at registrar: ns1-ns11.${DOMAIN} A <ip>"
