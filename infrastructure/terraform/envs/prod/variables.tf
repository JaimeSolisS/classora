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

variable "acme_ca" {
  description = "Caddy's ACME CA: production, or staging for automated tests only (browsers reject staging certificates on .dev)."
  type        = string
  default     = "production"
}
