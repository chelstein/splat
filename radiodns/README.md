# RadioDNS CDN-First Backbone

CDN-first RadioDNS backbone for **zerotrustradio.org** on DigitalOcean.

## Architecture

- **DigitalOcean Spaces CDN** — serves all public RadioDNS assets
- **DigitalOcean App Platform** — HTTP control plane (radiodns-api + publisher worker)
- **PowerDNS Authoritative 4.9** — hidden-master + ns1-ns11 secondary model
  - Hidden master: PostgreSQL backend, DNSSEC inline-signing, API private-only
  - Secondaries: lmdb backend, TSIG-authenticated AXFR/IXFR, 53/udp+tcp only

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
│   └── app.yaml                     App Platform declarative spec
├── dns/
│   ├── master/
│   │   ├── pdns.conf                Hidden master PowerDNS config
│   │   ├── entrypoint.sh            Secret injection at container start
│   │   └── docker-compose.yml       Master + PostgreSQL stack
│   ├── secondary/
│   │   ├── pdns.conf                Secondary (ns1-ns11) config
│   │   └── docker-compose.yml       Secondary-only stack
│   ├── schema/
│   │   └── pdns-gpgsql.sql          PostgreSQL schema for gpgsql backend
│   ├── scripts/
│   │   ├── provision-zone.sh        Create zone + records via PowerDNS API
│   │   ├── setup-tsig.sh            Generate TSIG key, assign to zone
│   │   ├── enable-dnssec.sh         DNSSEC setup via pdnsutil
│   │   ├── add-secondary.sh         Register secondary with master
│   │   └── verify-zone.sh           Full test suite
│   └── prometheus.yml               DNS metrics scraping
├── docs/
│   └── architecture.md
├── infra/terraform/
│   ├── main.tf                      Provider + remote state
│   ├── variables.tf
│   ├── outputs.tf
│   ├── spaces.tf                    5 Spaces buckets
│   ├── cdn.tf                       5 CDN endpoints + wildcard cert
│   ├── dns.tf                       DNS node vars + cloud-init outputs
│   ├── app-platform.tf              App Platform app
│   ├── cloud-init-master.yaml.tpl   Hidden master node bring-up
│   └── cloud-init-secondary.yaml.tpl  Secondary node bring-up
├── scripts/
│   ├── publish-spi.sh
│   ├── publish-logos.sh
│   ├── publish-vis.sh
│   ├── publish-evidence.sh
│   └── invalidate-cdn.sh
└── services/
    ├── api/                         FastAPI control plane
    └── publisher/                   Async Spaces writer worker
```

## Quickstart

```bash
# 1. Provision CDN + App Platform
cd radiodns/infra/terraform && terraform apply ...

# 2. Start hidden master
cd radiodns/dns/master
PDNS_DB_PASSWORD=... PDNS_API_KEY=... docker compose up -d

# 3. Provision zone
export PDNS_API_URL=http://hidden-master:8081 PDNS_API_KEY=...
radiodns/dns/scripts/provision-zone.sh

# 4. TSIG + DNSSEC
radiodns/dns/scripts/setup-tsig.sh
docker exec pdns-master radiodns/dns/scripts/enable-dnssec.sh

# 5. Bring up secondaries (repeat for ns1-ns11)
radiodns/dns/scripts/add-secondary.sh <ip> ns1.zerotrustradio.org

# 6. Verify
radiodns/dns/scripts/verify-zone.sh
```
