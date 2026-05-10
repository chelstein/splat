output "cdn_endpoints" {
  description = "Raw DO CDN endpoint hostnames (before custom domain activation)"
  value = {
    spi      = digitalocean_cdn.spi.endpoint
    logos    = digitalocean_cdn.logos.endpoint
    epg      = digitalocean_cdn.epg.endpoint
    vis      = digitalocean_cdn.vis.endpoint
    evidence = digitalocean_cdn.evidence.endpoint
  }
}

output "cdn_custom_domains" {
  description = "Public CDN-backed URLs for each asset class"
  value = {
    spi      = "https://spi.${var.domain}"
    logos    = "https://logos.${var.domain}"
    epg      = "https://epg.${var.domain}"
    vis      = "https://vis.${var.domain}"
    evidence = "https://evidence.${var.domain}"
  }
}

output "spaces_buckets" {
  description = "Spaces bucket names"
  value = {
    spi      = digitalocean_spaces_bucket.spi.name
    logos    = digitalocean_spaces_bucket.logos.name
    epg      = digitalocean_spaces_bucket.epg.name
    vis      = digitalocean_spaces_bucket.vis.name
    evidence = digitalocean_spaces_bucket.evidence.name
  }
}

output "app_platform_url" {
  description = "App Platform live URL for the RadioDNS control plane"
  value       = digitalocean_app.radiodns_control_plane.live_url
}

output "dns_verification" {
  description = "Expected DNS records — verify with: dig <name> <type>"
  value = {
    spi_cname      = "spi.${var.domain}      CNAME  ${digitalocean_cdn.spi.endpoint}"
    logos_cname    = "logos.${var.domain}    CNAME  ${digitalocean_cdn.logos.endpoint}"
    epg_cname      = "epg.${var.domain}      CNAME  ${digitalocean_cdn.epg.endpoint}"
    vis_cname      = "vis.${var.domain}      CNAME  ${digitalocean_cdn.vis.endpoint}"
    evidence_cname = "evidence.${var.domain} CNAME  ${digitalocean_cdn.evidence.endpoint}"
    srv_radiospi   = "_radiospi._tcp.${var.domain}  SRV  0 0 443 spi.${var.domain}"
    srv_radioepg   = "_radioepg._tcp.${var.domain}  SRV  0 0 443 epg.${var.domain}"
    srv_radiovis   = "_radiovis._tcp.${var.domain}  SRV  0 0 443 vis.${var.domain}"
    srv_radiotag   = "_radiotag._tcp.${var.domain}  SRV  0 0 443 api.${var.domain}"
  }
}
