variable "aws_profile" {
  description = "Local AWS CLI profile used by the provider."
  type        = string
  default     = "jsolisdev"
}

# The state bucket name is account-specific and never committed: terraform.sh
# exports it from the git-ignored backend.hcl as TF_VAR_state_bucket.
variable "state_bucket" {
  description = "Terraform state bucket (for the bootstrap and shared outputs)."
  type        = string
}

variable "instance_type" {
  description = "DEV host size (ADR-0010: t4g.small; t4g.medium only when monitoring is colocated)."
  type        = string
  default     = "t4g.small"
}

variable "acme_ca" {
  description = "Caddy's ACME CA: production, or staging for automated tests only (browsers reject staging certificates on .dev)."
  type        = string
  default     = "production"
}
