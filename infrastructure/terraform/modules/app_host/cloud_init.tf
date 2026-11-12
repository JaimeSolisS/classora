# User data (no secrets, ADR-0012): the host settings, the host scripts and
# systemd units from infrastructure/host/, then host-setup.sh. Gzipped, since
# the scripts would not fit the 16 KiB limit as plain text.
locals {
  host_files = merge(
    { for name in fileset("${local.host_dir}/bin", "*.sh") : "/opt/classora/bin/${name}" => {
      content     = filebase64("${local.host_dir}/bin/${name}")
      permissions = "0755"
    } },
    { for name in fileset("${local.host_dir}/systemd", "*") : "/opt/classora/systemd/${name}" => {
      content     = filebase64("${local.host_dir}/systemd/${name}")
      permissions = "0644"
    } },
  )

  cloud_init = templatefile("${local.host_dir}/cloud-init.yaml", {
    host_env = base64encode(join("", [
      "CLASSORA_ENV=${var.env}\n",
      "AWS_REGION=${local.region}\n",
      "OPS_BUCKET=${aws_s3_bucket.ops.bucket}\n",
      "DATA_VOLUME_ID=${aws_ebs_volume.data.id}\n",
    ]))
    files = local.host_files
  })
}
