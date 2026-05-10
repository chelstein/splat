# RadioDNS CDN-First Backbone Architecture

## Overview

All public RadioDNS assets are served exclusively from DigitalOcean Spaces CDN.
DNS nodes are minimal hosts that serve only the DNS protocol (UDP/TCP 53).
HTTP control-plane logic runs on DigitalOcean App Platform.

```
Radio receiver / client
        │
        ├─ DNS lookup ──────────────► CoreDNS node (UDP/TCP 53 only)
        │                              SRV  _radiospi._tcp → spi.zerotrustradio.org
        │                              CNAME spi.zerotrustradio.org → CDN endpoint
        │                              TXT, DNSSEC
        │                              (no SPI XML, no logos served here)
        │
        └─ HTTPS asset fetch ───────► DO Spaces CDN (global edge)
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
```

## CDN Hostnames

| Hostname | Asset class | Spaces bucket |
|---|---|---|
| `spi.zerotrustradio.org` | SI.xml, SPI XML snapshots | `ztr-spi` |
| `logos.zerotrustradio.org` | Station logos | `ztr-logos` |
| `epg.zerotrustradio.org` | EPG XML documents | `ztr-epg` |
| `vis.zerotrustradio.org` | RadioVIS slide images | `ztr-vis` |
| `evidence.zerotrustradio.org` | Validation artifacts, metadata snapshots | `ztr-evidence` |

All hostnames are CNAME records in the DO-managed DNS zone.
CNAMEs resolve to the Spaces CDN endpoint, never to a DNS node IP.

## Cache Strategy

| Asset | Cache-Control | Notes |
|---|---|---|
| Canonical SI.xml (live) | `public, max-age=60, s-maxage=600` | Short client TTL, longer CDN TTL |
| Versioned SPI snapshots | `public, max-age=3600` | Stable, moderate TTL |
| Station logos | `public, max-age=604800, immutable` | Hash filename, cache forever |
| EPG documents | `public, max-age=3600` | |
| RadioVIS slides | `public, max-age=3600` | Timestamp filename |
| Evidence artifacts | `public, max-age=86400, immutable` | Hash filename, write-once |
| App Platform frontend | CDN edge cache defaults | |

Cache-Control headers are set per-object at upload time via the publish scripts
or the publisher worker. The CDN endpoint TTL is a fallback only.

## Object Path Conventions

```
# Cache-busting versioned SPI snapshot
stations/{station_id}/spi/{version}/SI.xml

# Canonical live SI.xml (short TTL, updated in-place)
stations/{station_id}/spi/SI.xml

# Immutable content-addressed logo
stations/{station_id}/logos/{sha256}.png

# Timestamp-keyed RadioVIS slide
stations/{station_id}/vis/{unix_timestamp}.jpg

# Immutable content-addressed evidence artifact
evidence/{capture_id}/{sha256}.json
```

Logos and evidence artifacts use SHA-256 content hashes in the filename.
A new upload of identical content produces the same key and hits the CDN
cache immediately. A changed file produces a new key, bypassing the cache.

## DNS Records (DNS nodes only serve these)

```
; CNAME — CDN subdomains
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
@       TXT  "v=spf1 include:digitalocean.com ~all"
_dmarc  TXT  "v=DMARC1; p=quarantine; ..."
```

## DNS Node Deployment

Deploy only where UDP/TCP port 53 is required (authoritative NS hosts).
All other infrastructure uses App Platform or CDN.

```bash
# On each NS host
cd radiodns/dns
# Generate DNSSEC key pair
dnssec-keygen -a ED25519 -n ZONE zerotrustradio.org
mv Kzerotrustradio.org.* keys/
# Start CoreDNS
docker compose up -d
```

## Terraform Workflow

```bash
cd radiodns/infra/terraform

# First run — create the state bucket manually in DO console, then:
terraform init \
  -backend-config="access_key=$SPACES_ACCESS_KEY" \
  -backend-config="secret_key=$SPACES_SECRET_KEY"

terraform plan -var="do_token=$DO_TOKEN" \
               -var="spaces_access_id=$SPACES_ACCESS_KEY" \
               -var="spaces_secret_key=$SPACES_SECRET_KEY"

terraform apply  # creates all buckets, CDN endpoints, DNS records, App Platform app
```

## Publishing Assets

```bash
export AWS_ACCESS_KEY_ID=$SPACES_ACCESS_KEY
export AWS_SECRET_ACCESS_KEY=$SPACES_SECRET_KEY
export SPACES_REGION=nyc3

# Publish live SI.xml (short TTL)
./radiodns/scripts/publish-spi.sh bbc_radio4 live SI.xml

# Publish versioned SPI snapshot
./radiodns/scripts/publish-spi.sh bbc_radio4 v20240510 SI.xml

# Publish logo (immutable, hash filename)
./radiodns/scripts/publish-logos.sh bbc_radio4 logo.png

# Publish evidence artifact (immutable, hash filename)
./radiodns/scripts/publish-evidence.sh cap-20240510-001 capture.json

# Invalidate CDN cache (for live SI.xml after update)
export DO_TOKEN=...
./radiodns/scripts/invalidate-cdn.sh <spi-cdn-uuid> stations/bbc_radio4/spi/SI.xml
```
