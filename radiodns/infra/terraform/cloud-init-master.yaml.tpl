#cloud-config
# PowerDNS hidden master node
# Installs Docker, deploys pdns-master + postgres via Docker Compose.
# Port 8081 (PowerDNS API) is blocked externally by ufw.
# Only 53/udp and 53/tcp are open to the public internet.
#
# Terraform usage:
#   user_data = templatefile("cloud-init-master.yaml.tpl", {
#     pdns_db_password = var.pdns_db_password
#     pdns_api_key     = var.pdns_api_key
#   })

package_update: true
package_upgrade: true

packages:
  - docker.io
  - docker-compose-plugin
  - curl
  - jq
  - ufw

write_files:
  - path: /opt/radiodns/master/.env
    permissions: '0600'
    content: |
      PDNS_DB_PASSWORD=${pdns_db_password}
      PDNS_API_KEY=${pdns_api_key}

  - path: /etc/sysctl.d/99-pdns.conf
    content: |
      net.core.rmem_max=26214400
      net.core.wmem_max=26214400
      net.core.netdev_max_backlog=4096

runcmd:
  - sysctl -p /etc/sysctl.d/99-pdns.conf
  - systemctl enable --now docker

  # Firewall — DNS public, API private only
  - ufw --force enable
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw allow 22/tcp
  - ufw allow 53/udp
  - ufw allow 53/tcp
  - ufw allow from 10.0.0.0/8 to any port 8081
  - ufw allow from 172.16.0.0/12 to any port 8081
  - ufw allow from 192.168.0.0/16 to any port 8081
  - ufw deny 8081

  # Pull images
  - docker pull powerdns/pdns-auth-49:latest
  - docker pull postgres:16-alpine

  # Copy compose files from repo (assumes repo is cloned at /opt/radiodns)
  # In production, pull from Spaces or package into the image instead.
  - cd /opt/radiodns/master && docker compose --env-file .env up -d
