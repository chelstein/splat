# ---------------------------------------------------------------------------
# DNS — PowerDNS Authoritative hidden-master + secondary model
#
# Zone data for zerotrustradio.org lives in PowerDNS's PostgreSQL backend
# on the hidden master. Records are provisioned via the PowerDNS HTTP API
# using radiodns/dns/scripts/provision-zone.sh.
#
# This file manages:
#   - Variables for node IPs used in cloud-init templates
#   - Outputs documenting the expected DNS record set
#
# NOT managed here (previously used digitalocean_domain / digitalocean_record):
#   Zone records are in PowerDNS, not DigitalOcean DNS.
#   The domain registrar must delegate zerotrustradio.org to ns1-ns11
#   and provide glue A records for each NS host.
# ---------------------------------------------------------------------------

variable "pdns_db_password" {
  description = "PostgreSQL password for the PowerDNS gpgsql backend (hidden master)"
  type        = string
  sensitive   = true
}

variable "pdns_api_key" {
  description = "PowerDNS HTTP API key (hidden master only, private network)"
  type        = string
  sensitive   = true
}

variable "hidden_master_ip" {
  description = "Private IP of the hidden master PowerDNS node"
  type        = string
  default     = ""
}

variable "ns_ips" {
  description = "Public IP addresses for ns1-ns11 (list of 11 strings)"
  type        = list(string)
  default     = []
  validation {
    condition     = length(var.ns_ips) == 0 || length(var.ns_ips) == 11
    error_message = "ns_ips must be empty (defer setup) or exactly 11 values."
  }
}

# ---------------------------------------------------------------------------
# cloud-init templates rendered for master and secondary nodes
# Pass these as user_data when creating DO Droplets for DNS nodes.
# ---------------------------------------------------------------------------

data "template_file" "cloud_init_master" {
  template = file("${path.module}/cloud-init-master.yaml.tpl")
  vars = {
    pdns_db_password = var.pdns_db_password
    pdns_api_key     = var.pdns_api_key
  }
}

data "template_file" "cloud_init_secondary" {
  count    = length(var.ns_ips)
  template = file("${path.module}/cloud-init-secondary.yaml.tpl")
  vars = {
    ns_index = count.index + 1
  }
}

# ---------------------------------------------------------------------------
# Outputs — reference record set (configure in PowerDNS via provision-zone.sh)
# ---------------------------------------------------------------------------

output "pdns_zone_records" {
  description = "Expected records in PowerDNS for zerotrustradio.org (provisioned via provision-zone.sh)"
  value = {
    ns = [
      for i in range(1, 12) : "ns${i}.${var.domain}."
    ]
    cname_spi      = "spi.${var.domain}.      CNAME  ztr-spi.nyc3.cdn.digitaloceanspaces.com."
    cname_logos    = "logos.${var.domain}.    CNAME  ztr-logos.nyc3.cdn.digitaloceanspaces.com."
    cname_epg      = "epg.${var.domain}.      CNAME  ztr-epg.nyc3.cdn.digitaloceanspaces.com."
    cname_vis      = "vis.${var.domain}.      CNAME  ztr-vis.nyc3.cdn.digitaloceanspaces.com."
    cname_evidence = "evidence.${var.domain}. CNAME  ztr-evidence.nyc3.cdn.digitaloceanspaces.com."
    srv_radiospi   = "_radiospi._tcp.${var.domain}. SRV 0 0 443 spi.${var.domain}."
    srv_radioepg   = "_radioepg._tcp.${var.domain}. SRV 0 0 443 epg.${var.domain}."
    srv_radiovis   = "_radiovis._tcp.${var.domain}. SRV 0 0 443 vis.${var.domain}."
    srv_radiotag   = "_radiotag._tcp.${var.domain}. SRV 0 0 443 api.${var.domain}."
  }
}

output "pdns_cloud_init_master" {
  description = "Rendered cloud-init for the hidden master Droplet"
  value       = data.template_file.cloud_init_master.rendered
  sensitive   = true
}
