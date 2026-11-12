output "otlp_endpoint_parameter" {
  description = "The shared parameter the app hosts read (ADR-0014)."
  value       = aws_ssm_parameter.otlp_endpoint.name
}

output "grafana_admin_password_parameter" {
  description = "Grafana's admin password (SecureString): aws ssm get-parameter --with-decryption --name <this>."
  value       = aws_ssm_parameter.secret["grafana_admin_password"].name
}

output "smtp_user" {
  description = "The IAM user whose SES SMTP credentials Grafana uses."
  value       = aws_iam_user.ses_smtp.name
}
