# RadioDNS CDN-First Backbone

CDN-first RadioDNS backbone for **zerotrustradio.org** on DigitalOcean.

## Architecture

- **DigitalOcean Spaces CDN** — serves all public RadioDNS assets
- **DigitalOcean App Platform** — HTTP control plane (radiodns-api + publisher worker)
- **CoreDNS** — minimal DNS nodes serving CNAME / SRV / TXT / DNSSEC only

See [docs/architecture.md](docs/architecture.md) for the full design.

## Public CDN Hostnames

| Hostname | Content |
|---|---|
| `spi.zerotrustradio.org` | SI.xml, SPI XML snapshots |
| `logos.zerotrustradio.org` | Station logos |
| `epg.zerotrustradio.org` | EPG XML documents |
| `vis.zerotrustradio.org` | RadioVIS slide images |
| `evidence.zerotrustradio.org` | Validation artifacts, metadata snapshots |

## Directory Layout

```
radiodns/
├── app-platform/
│   └── app.yaml                  App Platform declarative spec
├── dns/
│   ├── Corefile                  CoreDNS config (DNS-only, no asset serving)
│   ├── docker-compose.yml        DNS node deployment
│   ├── prometheus.yml            DNS metrics scraping
│   └── zones/
│       └── zerotrustradio.org.zone  Authoritative zone file
├── docs/
│   └── architecture.md
├── infra/terraform/
│   ├── main.tf                   Provider + remote state
│   ├── variables.tf
│   ├── outputs.tf
│   ├── spaces.tf                 5 Spaces buckets
│   ├── cdn.tf                    5 CDN endpoints + wildcard cert
│   ├── dns.tf                    CNAME / SRV / TXT records
│   └── app-platform.tf           App Platform app
├── scripts/
│   ├── publish-spi.sh            Upload SI.xml with correct TTL
│   ├── publish-logos.sh          Upload logo with hash filename
│   ├── publish-vis.sh            Upload RadioVIS slide
│   ├── publish-evidence.sh       Upload evidence artifact with hash filename
│   └── invalidate-cdn.sh         Purge DO CDN cache
└── services/
    ├── api/                      FastAPI control plane
    │   ├── Dockerfile
    │   ├── main.py
    │   └── requirements.txt
    └── publisher/                Async Spaces writer worker
        ├── Dockerfile
        ├── worker.py
        └── requirements.txt
```

## Quickstart

```bash
# Provision all infrastructure
cd radiodns/infra/terraform
terraform init -backend-config="access_key=$SPACES_ACCESS_KEY" \
               -backend-config="secret_key=$SPACES_SECRET_KEY"
terraform apply -var="do_token=$DO_TOKEN" ...

# Deploy DNS nodes
cd radiodns/dns && docker compose up -d

# Publish assets
export AWS_ACCESS_KEY_ID=$SPACES_ACCESS_KEY
export AWS_SECRET_ACCESS_KEY=$SPACES_SECRET_KEY
./radiodns/scripts/publish-spi.sh <station_id> live SI.xml
./radiodns/scripts/publish-logos.sh <station_id> logo.png
```
