# ---------------------------------------------------------------------------
# Wildcard TLS certificate — covers all *.zerotrustradio.org CDN subdomains
# ---------------------------------------------------------------------------

resource "digitalocean_certificate" "wildcard" {
  name    = var.certificate_name
  type    = "lets_encrypt"
  domains = [
    var.domain,
    "*.${var.domain}",
  ]

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# CDN endpoints — one per Spaces bucket, each with a CDN-backed custom domain
#
# TTL here is the CDN fallback only.  Per-object Cache-Control headers set at
# upload time take precedence and implement the actual cache strategy:
#
#   logos        public, max-age=604800, immutable   (hashed filename)
#   stable SPI   public, max-age=3600
#   live SI.xml  public, max-age=60, s-maxage=600
#   evidence     public, max-age=86400, immutable    (hashed filename)
#   EPG / VIS    public, max-age=3600
# ---------------------------------------------------------------------------

resource "digitalocean_cdn" "spi" {
  origin           = digitalocean_spaces_bucket.spi.bucket_domain_name
  custom_domain    = "spi.${var.domain}"
  certificate_name = digitalocean_certificate.wildcard.name
  ttl              = 600 # fallback; live SI.xml objects carry max-age=60

  depends_on = [digitalocean_spaces_bucket.spi, digitalocean_certificate.wildcard]
}

resource "digitalocean_cdn" "logos" {
  origin           = digitalocean_spaces_bucket.logos.bucket_domain_name
  custom_domain    = "logos.${var.domain}"
  certificate_name = digitalocean_certificate.wildcard.name
  ttl              = 604800

  depends_on = [digitalocean_spaces_bucket.logos, digitalocean_certificate.wildcard]
}

resource "digitalocean_cdn" "epg" {
  origin           = digitalocean_spaces_bucket.epg.bucket_domain_name
  custom_domain    = "epg.${var.domain}"
  certificate_name = digitalocean_certificate.wildcard.name
  ttl              = 3600

  depends_on = [digitalocean_spaces_bucket.epg, digitalocean_certificate.wildcard]
}

resource "digitalocean_cdn" "vis" {
  origin           = digitalocean_spaces_bucket.vis.bucket_domain_name
  custom_domain    = "vis.${var.domain}"
  certificate_name = digitalocean_certificate.wildcard.name
  ttl              = 3600

  depends_on = [digitalocean_spaces_bucket.vis, digitalocean_certificate.wildcard]
}

resource "digitalocean_cdn" "evidence" {
  origin           = digitalocean_spaces_bucket.evidence.bucket_domain_name
  custom_domain    = "evidence.${var.domain}"
  certificate_name = digitalocean_certificate.wildcard.name
  ttl              = 86400

  depends_on = [digitalocean_spaces_bucket.evidence, digitalocean_certificate.wildcard]
}
