output "instance_id" {
  description = "Monitoring host (Session Manager: aws ssm start-session --target <id>)."
  value       = module.monitoring_host.instance_id
}

output "private_ip" {
  description = "The gateway's private address (/classora/shared/otlp_endpoint)."
  value       = module.monitoring_host.private_ip
}

output "data_volume_id" {
  description = "Monitoring data volume (telemetry stores, Grafana, certificates)."
  value       = module.monitoring_host.data_volume_id
}

output "ops_bucket" {
  description = "Monitoring ops bucket (releases/monitoring.tgz)."
  value       = module.monitoring_host.ops_bucket
}

output "security_group_id" {
  description = "Monitoring host security group."
  value       = module.monitoring_host.security_group_id
}

output "ingest_sources" {
  description = "Security groups admitted on 4318."
  value       = sort(data.aws_security_groups.app_hosts.ids)
}

output "grafana_url" {
  description = "Grafana over HTTPS."
  value       = module.monitoring_host.grafana_url
}

output "grafana_admin_password_parameter" {
  description = "aws ssm get-parameter --with-decryption --name <this>"
  value       = module.monitoring_host.grafana_admin_password_parameter
}

output "sync_document" {
  description = "The SSM document make monitoring-sync sends."
  value       = module.monitoring_host.sync_document
}
