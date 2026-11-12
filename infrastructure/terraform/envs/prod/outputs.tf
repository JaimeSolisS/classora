output "instance_id" {
  description = "PROD host (Session Manager: aws ssm start-session --target <id>)."
  value       = module.app_host.instance_id
}

output "data_volume_id" {
  description = "PROD data volume (PostgreSQL, Caddy certificates, collector buffers)."
  value       = module.app_host.data_volume_id
}

output "ops_bucket" {
  description = "PROD ops bucket (release bundles under releases/, backups under backups/)."
  value       = module.app_host.ops_bucket
}

output "hostnames" {
  description = "PROD hostnames."
  value       = module.app_host.hostnames
}

output "deploy_document" {
  description = "PROD deploy SSM document (release-prod.yml)."
  value       = module.app_host.deploy_document
}

output "snapshot_policy_id" {
  description = "PROD data volume snapshot policy (Data Lifecycle Manager)."
  value       = module.app_host.snapshot_policy_id
}
