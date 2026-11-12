# User data (no secrets, ADR-0012): the host settings, the scripts and units
# the monitoring host needs, then host-setup.sh (the monitoring host's own,
# installed under the name the shared cloud-init.yaml runs). The stack's
# configuration comes later, from the synced bundle.
locals {
  host_dir = "${path.module}/../../../host"

  monitoring_files = {
    "/opt/classora/bin/install-docker.sh"               = { src = "bin/install-docker.sh", permissions = "0755" }
    "/opt/classora/bin/mount-data-volume.sh"            = { src = "bin/mount-data-volume.sh", permissions = "0755" }
    "/opt/classora/bin/render-monitoring-env.sh"        = { src = "bin/render-monitoring-env.sh", permissions = "0755" }
    "/opt/classora/bin/monitoring-compose.sh"           = { src = "bin/monitoring-compose.sh", permissions = "0755" }
    "/opt/classora/bin/monitoring-up.sh"                = { src = "bin/monitoring-up.sh", permissions = "0755" }
    "/opt/classora/bin/host-setup.sh"                   = { src = "monitoring/host-setup.sh", permissions = "0755" }
    "/opt/classora/bin/deploy-monitoring.sh"            = { src = "monitoring/deploy-monitoring.sh", permissions = "0755" }
    "/opt/classora/systemd/classora-monitoring.service" = { src = "systemd/classora-monitoring.service", permissions = "0644" }
    "/opt/classora/systemd/docker-classora.conf"        = { src = "systemd/docker-classora.conf", permissions = "0644" }
  }

  cloud_init = templatefile("${local.host_dir}/cloud-init.yaml", {
    host_env = base64encode(join("", [
      "CLASSORA_ENV=monitoring\n",
      "AWS_REGION=${local.region}\n",
      "OPS_BUCKET=${aws_s3_bucket.ops.bucket}\n",
      "DATA_VOLUME_ID=${aws_ebs_volume.data.id}\n",
    ]))
    files = { for path, f in local.monitoring_files : path => {
      content     = file("${local.host_dir}/${f.src}")
      permissions = f.permissions
    } }
  })
}
