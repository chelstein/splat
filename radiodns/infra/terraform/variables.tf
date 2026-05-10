variable "do_token" {
  description = "DigitalOcean API token"
  type        = string
  sensitive   = true
}

variable "spaces_access_id" {
  description = "DigitalOcean Spaces access key ID"
  type        = string
  sensitive   = true
}

variable "spaces_secret_key" {
  description = "DigitalOcean Spaces secret key"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "DigitalOcean region for Spaces buckets"
  type        = string
  default     = "nyc3"
}

variable "domain" {
  description = "Root domain for all RadioDNS hostnames"
  type        = string
  default     = "zerotrustradio.org"
}

variable "certificate_name" {
  description = "Name for the DigitalOcean managed Let's Encrypt wildcard certificate"
  type        = string
  default     = "ztr-wildcard"
}

variable "app_platform_region" {
  description = "App Platform region slug"
  type        = string
  default     = "nyc"
}

variable "github_repo" {
  description = "GitHub repository supplying App Platform source (owner/repo)"
  type        = string
  default     = "chelstein/splat"
}

variable "github_branch" {
  description = "GitHub branch for App Platform deployments"
  type        = string
  default     = "main"
}
