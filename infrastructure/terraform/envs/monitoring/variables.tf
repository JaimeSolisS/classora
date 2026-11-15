variable "aws_profile" {
  description = "Local AWS CLI profile used by the provider."
  type        = string
  default     = "jsolisdev"
}

# Exported by terraform.sh from the git-ignored backend.hcl (#614 D14).
variable "state_bucket" {
  description = "Terraform state bucket (for the bootstrap and shared outputs)."
  type        = string
}

variable "alert_email" {
  description = "Alert recipient, a verified SES identity (bootstrap). Never committed: export TF_VAR_alert_email."
  type        = string
  sensitive   = true
}

variable "retention_metrics_days" {
  description = "Prometheus retention (ADR-0014: 15 days)."
  type        = number
  default     = 15
}

variable "retention_logs_days" {
  description = "Loki retention (ADR-0014: 7 days)."
  type        = number
  default     = 7
}

variable "retention_traces_days" {
  description = "Tempo retention (ADR-0014: 7 days)."
  type        = number
  default     = 7
}
