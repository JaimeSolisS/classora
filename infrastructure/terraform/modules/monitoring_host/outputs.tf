output "instance_id" {
  description = "The monitoring host's instance ID (Session Manager)."
  value       = aws_instance.this.id
}

output "private_ip" {
  description = "The gateway's private address (agents export to http://<this>:4318)."
  value       = aws_network_interface.host.private_ip
}

output "data_volume_id" {
  description = "The data volume's ID."
  value       = aws_ebs_volume.data.id
}

output "ops_bucket" {
  description = "The ops bucket (releases/monitoring.tgz)."
  value       = aws_s3_bucket.ops.bucket
}

output "security_group_id" {
  description = "The monitoring host's security group."
  value       = aws_security_group.host.id
}

output "grafana_url" {
  description = "Grafana over HTTPS."
  value       = "https://${var.grafana_host}/"
}

output "grafana_admin_password_parameter" {
  description = "aws ssm get-parameter --with-decryption --name <this>"
  value       = module.config.grafana_admin_password_parameter
}

output "sync_document" {
  description = "The SSM document make monitoring-sync sends."
  value       = aws_ssm_document.sync.name
}
