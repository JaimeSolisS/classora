output "instance_id" {
  description = "DEV host (Session Manager: aws ssm start-session --target <id>)."
  value       = module.app_host.instance_id
}

output "data_volume_id" {
  description = "DEV data volume (PostgreSQL, Caddy certificates, collector buffers)."
  value       = module.app_host.data_volume_id
}

output "ops_bucket" {
  description = "DEV ops bucket (release bundles under releases/, backups under backups/)."
  value       = module.app_host.ops_bucket
}

output "hostnames" {
  description = "DEV hostnames."
  value       = module.app_host.hostnames
}

output "deploy_document" {
  description = "DEV deploy SSM document (deploy-dev.yml)."
  value       = module.app_host.deploy_document
}
