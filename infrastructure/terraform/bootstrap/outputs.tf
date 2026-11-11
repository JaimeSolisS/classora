output "state_bucket" {
  description = "State bucket name, for infrastructure/terraform/backend.hcl."
  value       = aws_s3_bucket.state.bucket
}

output "archive_bucket" {
  description = "Archive bucket for the final PROD dump (ADR-0013)."
  value       = aws_s3_bucket.archive.bucket
}

output "hosted_zone_id" {
  description = "Adopted Route 53 zone."
  value       = aws_route53_zone.main.zone_id
}

output "ses_domain_identity" {
  description = "SES domain identity (alerts are sent from alerts@<domain>)."
  value       = aws_sesv2_email_identity.domain.email_identity
}

output "gha_role_arns" {
  description = "Deploy role ARNs, set as GitHub repository variables by M9-07 / M9-10."
  value       = { for key, role in aws_iam_role.gha : key => role.arn }
}
