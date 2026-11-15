variable "aws_profile" {
  description = "Local AWS CLI profile used by the provider."
  type        = string
  default     = "jsolisdev"
}

variable "az" {
  description = "The single Availability Zone every host runs in (ADR-0010)."
  type        = string
  default     = "us-east-2a"
}

variable "vpc_cidr" {
  description = "VPC address range."
  type        = string
  default     = "10.40.0.0/16"
}

variable "subnets" {
  description = "One public /24 per host role."
  type        = map(string)
  default = {
    dev        = "10.40.1.0/24"
    prod       = "10.40.2.0/24"
    monitoring = "10.40.3.0/24"
  }
}

variable "ecr_repositories" {
  description = "ECR repositories (ADR-0011): API and worker, edge web, PgBouncer."
  type        = set(string)
  default     = ["classora/api", "classora/web", "classora/pgbouncer"]
}
