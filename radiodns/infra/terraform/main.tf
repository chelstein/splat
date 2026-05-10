terraform {
  required_version = ">= 1.5"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }

  backend "s3" {
    # DigitalOcean Spaces as Terraform remote state backend
    endpoint                    = "https://nyc3.digitaloceanspaces.com"
    region                      = "us-east-1" # required by the S3 backend, ignored by DO
    bucket                      = "ztr-terraform-state"
    key                         = "radiodns/terraform.tfstate"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    force_path_style            = true
  }
}

provider "digitalocean" {
  token             = var.do_token
  spaces_access_id  = var.spaces_access_id
  spaces_secret_key = var.spaces_secret_key
}
