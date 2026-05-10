# ---------------------------------------------------------------------------
# App Platform — HTTP control plane
# radiodns-api:       REST API for station registration and SPI management
# radiodns-publisher: async worker that writes assets to Spaces with correct
#                     Cache-Control headers and object paths
# ---------------------------------------------------------------------------

resource "digitalocean_app" "radiodns_control_plane" {
  spec {
    name   = "radiodns-control-plane"
    region = var.app_platform_region

    domain {
      domain = "api.${var.domain}"
      type   = "PRIMARY"
      zone   = var.domain
    }

    # Shared env vars available to all components
    env {
      key   = "SPACES_REGION"
      value = var.region
      scope = "RUN_TIME"
    }
    env {
      key   = "SPI_BUCKET"
      value = "ztr-spi"
      scope = "RUN_TIME"
    }
    env {
      key   = "LOGOS_BUCKET"
      value = "ztr-logos"
      scope = "RUN_TIME"
    }
    env {
      key   = "EPG_BUCKET"
      value = "ztr-epg"
      scope = "RUN_TIME"
    }
    env {
      key   = "VIS_BUCKET"
      value = "ztr-vis"
      scope = "RUN_TIME"
    }
    env {
      key   = "EVIDENCE_BUCKET"
      value = "ztr-evidence"
      scope = "RUN_TIME"
    }
    env {
      key   = "SPI_CDN_HOST"
      value = "spi.${var.domain}"
      scope = "RUN_TIME"
    }
    env {
      key   = "LOGOS_CDN_HOST"
      value = "logos.${var.domain}"
      scope = "RUN_TIME"
    }
    env {
      key   = "EPG_CDN_HOST"
      value = "epg.${var.domain}"
      scope = "RUN_TIME"
    }
    env {
      key   = "VIS_CDN_HOST"
      value = "vis.${var.domain}"
      scope = "RUN_TIME"
    }
    env {
      key   = "EVIDENCE_CDN_HOST"
      value = "evidence.${var.domain}"
      scope = "RUN_TIME"
    }
    env {
      key   = "DO_SPACES_ACCESS_KEY"
      value = var.spaces_access_id
      scope = "RUN_TIME"
      type  = "SECRET"
    }
    env {
      key   = "DO_SPACES_SECRET_KEY"
      value = var.spaces_secret_key
      scope = "RUN_TIME"
      type  = "SECRET"
    }

    service {
      name               = "radiodns-api"
      instance_count     = 1
      instance_size_slug = "apps-s-1vcpu-0.5gb"
      http_port          = 8080

      github {
        repo           = var.github_repo
        branch         = var.github_branch
        deploy_on_push = true
      }

      source_dir      = "radiodns/services/api"
      dockerfile_path = "radiodns/services/api/Dockerfile"

      health_check {
        http_path             = "/health"
        initial_delay_seconds = 10
        period_seconds        = 30
        timeout_seconds       = 5
        failure_threshold     = 3
        success_threshold     = 1
      }
    }

    worker {
      name               = "radiodns-publisher"
      instance_count     = 1
      instance_size_slug = "apps-s-1vcpu-0.5gb"

      github {
        repo           = var.github_repo
        branch         = var.github_branch
        deploy_on_push = true
      }

      source_dir      = "radiodns/services/publisher"
      dockerfile_path = "radiodns/services/publisher/Dockerfile"
    }
  }
}
