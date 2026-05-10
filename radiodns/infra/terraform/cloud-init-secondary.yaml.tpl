#cloud-config
# PowerDNS public secondary node (ns1-ns11)
# Installs Docker, deploys pdns-secondary (lmdb backend).
# Exposes 53/udp and 53/tcp only — port 8081 blocked.
# PowerDNS API is disabled in pdns.conf.
#
# Terraform usage:
#   user_data = templatefile("cloud-init-secondary.yaml.tpl", {
#     ns_index = 1  # 1-11
#   })

package_update: true
package_upgrade: true

packages:
  - docker.io
  - docker-compose-plugin
  - ufw

write_files:
  - path: /etc/sysctl.d/99-pdns.conf
    content: |
      net.core.rmem_max=26214400
      net.core.wmem_max=26214400
      net.core.netdev_max_backlog=4096

runcmd:
  - sysctl -p /etc/sysctl.d/99-pdns.conf
  - systemctl enable --now docker

  # Firewall — DNS only; block API port, block everything else
  - ufw --force enable
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw allow 22/tcp
  - ufw allow 53/udp
  - ufw allow 53/tcp
  - ufw deny 8081

  # Pull image
  - docker pull powerdns/pdns-auth-49:latest

  # Start secondary (TSIG and slave zone configured via add-secondary.sh)
  - cd /opt/radiodns/secondary && docker compose up -d
