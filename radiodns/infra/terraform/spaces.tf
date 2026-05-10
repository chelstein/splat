# ---------------------------------------------------------------------------
# Spaces buckets — one per CDN hostname, all net-new
# Public-read with CORS for GET/HEAD from any origin.
# Cache-Control headers are set per-object at upload time (see scripts/).
# ---------------------------------------------------------------------------

resource "digitalocean_spaces_bucket" "spi" {
  name   = "ztr-spi"
  region = var.region
  acl    = "public-read"

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 86400
  }

  versioning {
    enabled = true
  }
}

resource "digitalocean_spaces_bucket" "logos" {
  name   = "ztr-logos"
  region = var.region
  acl    = "public-read"

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 604800
  }

  versioning {
    enabled = true
  }
}

resource "digitalocean_spaces_bucket" "epg" {
  name   = "ztr-epg"
  region = var.region
  acl    = "public-read"

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 86400
  }

  versioning {
    enabled = true
  }
}

resource "digitalocean_spaces_bucket" "vis" {
  name   = "ztr-vis"
  region = var.region
  acl    = "public-read"

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 86400
  }

  versioning {
    enabled = true
  }
}

resource "digitalocean_spaces_bucket" "evidence" {
  name   = "ztr-evidence"
  region = var.region
  acl    = "public-read"

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 86400
  }

  versioning {
    enabled = true
  }
}
