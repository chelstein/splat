# RadioDNS CDN-First Backbone Architecture

## Overview

All public RadioDNS assets are served exclusively from DigitalOcean Spaces CDN.
DNS nodes run PowerDNS Authoritative in a hidden-master / secondary model.
HTTP control-plane logic runs on DigitalOcean App Platform.
No DNS node serves XML, logos, images, or any static files.

```
Radio receiver / client
        │
        ├─ DNS lookup ──────────────► PowerDNS secondary (ns1-ns11)
        │                              Receives zone via TSIG-auth AXFR/IXFR
        │                              from hidden master.
        │                              SRV  _radiospi._tcp → spi.zerotrustradio.org
        │                              CNAME spi.zerotrustradio.org → CDN endpoint
        │                              TXT, DNSSEC (ECDSA P-256)
        │                              Exposes 53/udp + 53/tcp only.
        │                              API disabled. No assets served.
        │
        └─ HTTPS asset fetch ────────► DO Spaces CDN (global edge)
                                       spi.zerotrustradio.org      SPI XML / SI.xml
                                       logos.zerotrustradio.org    station logos
                                       epg.zerotrustradio.org      EPG XML
                                       vis.zerotrustradio.org      RadioVIS slides
                                       evidence.zerotrustradio.org validation artifacts

Broadcaster / operator
        │
        └─ HTTPS API ───────────────► App Platform (api.zerotrustradio.org)
                                       radiodns-api     REST control plane
                                       radiodns-publisher  async Spaces writer

Admin / operator
        │
        └─ PowerDNS API (private) ──► hidden-master.zerotrustradio.org:8081
                                       Zone management, TSIG, DNSSEC
                                       Private network + ufw only
```

## DNS Layer: PowerDNS Hidden-Master + Secondary Model

### Hidden Master

- **Host**: `hidden-master.zerotrustradio.org` (private IP, not in NS records)
- **Software**: PowerDNS Authoritative 4.9 + PostgreSQL 16 (gpgsql backend)
- **Role**: `primary=yes`, `secondary=no`
- **DNSSEC**: inline signing with ECDSA P-256 (KSK + ZSK)
- **API**: enabled on port 8081, restricted to private network by ufw
- **AXFR**: allowed from any IP; TSIG (hmac-sha256) required per zone
- **Exposed ports**: 53/udp, 53/tcp (DNS), 8081/tcp (API, private only)

### Public Secondaries (ns1–ns11)

- **Software**: PowerDNS Authoritative 4.9 (lmdb backend)
- **Role**: `secondary=yes`, `primary=no`
- **Zone transfer**: TSIG-authenticated AXFR/IXFR from hidden master
- **DNSSEC**: serves pre-signed records as received from master
- **API**: disabled (`api=no`, `webserver=no`)
- **Exposed ports**: 53/udp, 53/tcp only — ufw blocks 8081
- **No static files, no HTTP, no XML, no logos served**

### Zone Transfer Flow

```
hidden master (primary)
  │  PostgreSQL stores zone + DNSSEC keys
  │  Inline-signs all records
  │  Notifies all ALSO-NOTIFY IPs on zone change
  │
  └─ AXFR/IXFR (TSIG hmac-sha256) ──► ns1  ─┐
                                    ──► ns2  ─┤
                                    ──► ...  ─┤  Serve queries
                                    ──► ns11 ─┘  (pre-signed zone)
```

### TSIG Key Lifecycle

1. `setup-tsig.sh` generates key on master, sets `TSIG-ALLOW-AXFR` + `AXFR-MASTER-TSIG`
2. `add-secondary.sh` prints per-secondary `pdnsutil import-tsig-key` + `activate-tsig-key` commands
3. Zone transfers are refused without a valid TSIG signature

### DNSSEC

- `enable-dnssec.sh` calls `pdnsutil secure-zone` → generates KSK + ZSK (ECDSA P-256)
- NSEC3 opt-out narrow mode
- DS records exported via `pdnsutil export-zone-ds` for registrar submission
- Secondaries serve RRSIG, DNSKEY, NSEC3, DS as received from master AXFR

## CDN Hostnames

| Hostname | Asset class | Spaces bucket | CDN TTL fallback |
|---|---|---|---|
| `spi.zerotrustradio.org` | SI.xml, SPI XML snapshots | `ztr-spi` | 600s |
| `logos.zerotrustradio.org` | Station logos | `ztr-logos` | 604800s |
| `epg.zerotrustradio.org` | EPG XML documents | `ztr-epg` | 3600s |
| `vis.zerotrustradio.org` | RadioVIS slide images | `ztr-vis` | 3600s |
| `evidence.zerotrustradio.org` | Validation artifacts | `ztr-evidence` | 86400s |

All are CNAME records in PowerDNS pointing to the Spaces CDN endpoint.
DNS nodes never serve any of this content.

## Cache Strategy

| Asset | Cache-Control | Notes |
|---|---|---|
| Canonical SI.xml (live) | `public, max-age=60, s-maxage=600` | Short client TTL |
| Versioned SPI snapshots | `public, max-age=3600` | |
| Station logos | `public, max-age=604800, immutable` | SHA-256 hashed filename |
| EPG documents | `public, max-age=3600` | |
| RadioVIS slides | `public, max-age=3600` | Unix timestamp filename |
| Evidence artifacts | `public, max-age=86400, immutable` | SHA-256 hashed filename |

## Object Path Conventions

```
stations/{station_id}/spi/SI.xml                    live (short TTL)
stations/{station_id}/spi/{version}/SI.xml          versioned snapshot
stations/{station_id}/logos/{sha256}.png            immutable
stations/{station_id}/vis/{unix_timestamp}.jpg      timestamp-keyed
evidence/{capture_id}/{sha256}.json                 immutable
```

## DNS Records (managed in PowerDNS via provision-zone.sh)

```
; NS — public secondaries only; hidden master not listed
@  NS  ns1.zerotrustradio.org.
@  NS  ns2.zerotrustradio.org.
   ...  (ns3-ns11)

; CNAME — CDN subdomains (never point to DNS node IPs)
spi      CNAME  ztr-spi.nyc3.cdn.digitaloceanspaces.com.
logos    CNAME  ztr-logos.nyc3.cdn.digitaloceanspaces.com.
epg      CNAME  ztr-epg.nyc3.cdn.digitaloceanspaces.com.
vis      CNAME  ztr-vis.nyc3.cdn.digitaloceanspaces.com.
evidence CNAME  ztr-evidence.nyc3.cdn.digitaloceanspaces.com.
api      CNAME  zerotrustradio.org.ondigitalocean.app.

; SRV — RadioDNS bearer lookup (RFC 5507)
_radiospi._tcp  SRV  0 0 443  spi.zerotrustradio.org.
_radioepg._tcp  SRV  0 0 443  epg.zerotrustradio.org.
_radiovis._tcp  SRV  0 0 443  vis.zerotrustradio.org.
_radiotag._tcp  SRV  0 0 443  api.zerotrustradio.org.

; TXT
@        TXT  "v=spf1 include:digitalocean.com ~all"
_radiodns  TXT  "v=RadioDNS1 auth=zerotrustradio"
_dmarc   TXT  "v=DMARC1; p=quarantine; ..."
```

## Deployment Playbook

### 1. Provision infrastructure with Terraform

```bash
cd radiodns/infra/terraform
terraform apply \
  -var="do_token=$DO_TOKEN" \
  -var="pdns_db_password=$PDNS_DB_PASSWORD" \
  -var="pdns_api_key=$PDNS_API_KEY" \
  -var='ns_ips=["1.2.3.4","5.6.7.8",...]'   # 11 IPs
```

### 2. Start hidden master

```bash
cd radiodns/dns/master
PDNS_DB_PASSWORD=... PDNS_API_KEY=... docker compose up -d
```

### 3. Provision zone records

```bash
export PDNS_API_URL=http://hidden-master:8081
export PDNS_API_KEY=...
export NS_IPS="1.2.3.4 5.6.7.8 ..."    # 11 IPs
radiodns/dns/scripts/provision-zone.sh
```

### 4. Configure TSIG

```bash
radiodns/dns/scripts/setup-tsig.sh
# → outputs TSIG_SECRET; export it
export TSIG_SECRET=...
```

### 5. Enable DNSSEC

```bash
# On the hidden master:
docker exec pdns-master radiodns/dns/scripts/enable-dnssec.sh
# Submit DS records to registrar
```

### 6. Bring up each secondary

```bash
export HIDDEN_MASTER_IP=10.0.0.1
for ip in ${NS_IPS}; do
  ssh root@${ip} 'cd /opt/radiodns/secondary && docker compose up -d'
  radiodns/dns/scripts/add-secondary.sh ${ip} ns${n}.zerotrustradio.org
done
```

### 7. Verify

```bash
radiodns/dns/scripts/verify-zone.sh
NS_HOST=ns1.zerotrustradio.org radiodns/dns/scripts/verify-zone.sh
```
