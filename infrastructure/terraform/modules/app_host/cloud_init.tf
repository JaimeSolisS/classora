# User data (no secrets, ADR-0012): the host settings, the host scripts,
# systemd units and logrotate config from infrastructure/host/, then
# host-setup.sh. Gzipped: EC2 allows 16 KiB of user data (about 11 KiB used).
# The backup and restore scripts (#616) aren't needed before the first
# release, so they come only with the release bundle (deploy.sh; on a
# replaced instance host-setup.sh installs them from the current release).
locals {
  release_only_scripts = ["backup.sh", "backup-common.sh", "restore.sh", "final-dump.sh"]

  host_files = merge(
    { for name in setsubtract(fileset("${local.host_dir}/bin", "*.sh"), local.release_only_scripts) : "/opt/classora/bin/${name}" => {
      content     = file("${local.host_dir}/bin/${name}")
      permissions = "0755"
    } },
    { for name in fileset("${local.host_dir}/systemd", "*") : "/opt/classora/systemd/${name}" => {
      content     = file("${local.host_dir}/systemd/${name}")
      permissions = "0644"
    } },
    { for name in fileset("${local.host_dir}/logrotate", "*") : "/opt/classora/logrotate/${name}" => {
      content     = file("${local.host_dir}/logrotate/${name}")
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
