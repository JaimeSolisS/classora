output "instance_id" {
  description = "The host's instance ID."
  value       = aws_instance.this.id
}

output "data_volume_id" {
  description = "The data volume's ID."
  value       = aws_ebs_volume.data.id
}

output "ops_bucket" {
  description = "The ops bucket's name."
  value       = aws_s3_bucket.ops.bucket
}

output "hostnames" {
  description = "Hostnames with an A record to the host's Elastic IP."
  value       = var.hostnames
}

output "security_group_id" {
  description = "The host's security group."
  value       = aws_security_group.host.id
}
