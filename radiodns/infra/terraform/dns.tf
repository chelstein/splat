# ---------------------------------------------------------------------------
# DNS — DigitalOcean-managed zone for zerotrustradio.org
#
# DNS nodes serve CNAME, SRV, TXT, and DNSSEC records only.
# No A records point to asset-serving hosts.
# All public RadioDNS content is served exclusively via CDN.
# ---------------------------------------------------------------------------

resource "digitalocean_domain" "root" {
  name = var.domain
}

# -- CNAME: CDN-backed public subdomains ---------------------------------

resource "digitalocean_record" "cname_spi" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "spi"
  value  = "${digitalocean_cdn.spi.endpoint}."
  ttl    = 300
}

resource "digitalocean_record" "cname_logos" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "logos"
  value  = "${digitalocean_cdn.logos.endpoint}."
  ttl    = 300
}

resource "digitalocean_record" "cname_epg" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "epg"
  value  = "${digitalocean_cdn.epg.endpoint}."
  ttl    = 300
}

resource "digitalocean_record" "cname_vis" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "vis"
  value  = "${digitalocean_cdn.vis.endpoint}."
  ttl    = 300
}

resource "digitalocean_record" "cname_evidence" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "evidence"
  value  = "${digitalocean_cdn.evidence.endpoint}."
  ttl    = 300
}

# App Platform control plane
resource "digitalocean_record" "cname_api" {
  domain = digitalocean_domain.root.name
  type   = "CNAME"
  name   = "api"
  value  = "${var.domain}.ondigitalocean.app."
  ttl    = 300
}

# -- SRV: RadioDNS service discovery (RFC 5507) --------------------------
# Clients resolve these to locate the SPI, EPG, VIS, and RadioTAG endpoints.
# All targets resolve to CDN or App Platform — never to bare DNS node IPs.

resource "digitalocean_record" "srv_radiospi" {
  domain   = digitalocean_domain.root.name
  type     = "SRV"
  name     = "_radiospi._tcp"
  value    = "spi.${var.domain}."
  priority = 0
  weight   = 0
  port     = 443
  ttl      = 300
}

resource "digitalocean_record" "srv_radioepg" {
  domain   = digitalocean_domain.root.name
  type     = "SRV"
  name     = "_radioepg._tcp"
  value    = "epg.${var.domain}."
  priority = 0
  weight   = 0
  port     = 443
  ttl      = 300
}

resource "digitalocean_record" "srv_radiovis" {
  domain   = digitalocean_domain.root.name
  type     = "SRV"
  name     = "_radiovis._tcp"
  value    = "vis.${var.domain}."
  priority = 0
  weight   = 0
  port     = 443
  ttl      = 300
}

resource "digitalocean_record" "srv_radiotag" {
  domain   = digitalocean_domain.root.name
  type     = "SRV"
  name     = "_radiotag._tcp"
  value    = "api.${var.domain}."
  priority = 0
  weight   = 0
  port     = 443
  ttl      = 300
}

# -- TXT: verification and policy ----------------------------------------

resource "digitalocean_record" "txt_spf" {
  domain = digitalocean_domain.root.name
  type   = "TXT"
  name   = "@"
  value  = "v=spf1 include:digitalocean.com ~all"
  ttl    = 3600
}

resource "digitalocean_record" "txt_radiodns" {
  domain = digitalocean_domain.root.name
  type   = "TXT"
  name   = "_radiodns"
  value  = "v=RadioDNS1 auth=zerotrustradio"
  ttl    = 3600
}

resource "digitalocean_record" "txt_dmarc" {
  domain = digitalocean_domain.root.name
  type   = "TXT"
  name   = "_dmarc"
  value  = "v=DMARC1; p=quarantine; rua=mailto:dmarc@${var.domain}"
  ttl    = 3600
}
