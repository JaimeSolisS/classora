# Personal values (hosted zone ID, alert e-mail) have no default and are never
# committed: pass them as TF_VAR_hosted_zone_id / TF_VAR_alert_email or in a
# git-ignored *.auto.tfvars file (ADR-0012).

variable "aws_profile" {
  description = "Local AWS CLI profile used by the provider."
  type        = string
  default     = "jsolisdev"
}

variable "domain_name" {
  description = "The registered domain whose Route 53 zone is adopted."
  type        = string
  default     = "classora.dev"
}

variable "hosted_zone_id" {
  description = "ID of the hosted zone the Route 53 registrar created (adopted with an import block)."
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]{8,32}$", var.hosted_zone_id))
    error_message = "hosted_zone_id must be a Route 53 zone ID such as Z0123456789ABC."
  }
}

variable "hosted_zone_comment" {
  description = "Existing comment of the adopted zone (kept so the import changes nothing but tags)."
  type        = string
  default     = "HostedZone created by Route53 Registrar"
}

variable "alert_email" {
  description = "Owner e-mail for SES alerts and budget notifications (verified SES recipient)."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be an e-mail address."
  }
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose Actions environments may assume the deploy roles."
  type        = string
  default     = "JaimeSolisS/classora"
}

variable "state_admin_arn" {
  description = "The only principal allowed to use the state bucket. Defaults to the identity running Terraform."
  type        = string
  default     = null
}

variable "budget_limit_usd" {
  description = "Monthly budget for Project=classora costs (decision 9)."
  type        = number
  default     = 75
}
