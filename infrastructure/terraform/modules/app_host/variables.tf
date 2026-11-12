variable "env" {
  description = "Environment name (dev or prod): tags, names and the /classora/<env>/ parameter path."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod."
  }
}

variable "instance_type" {
  description = "EC2 instance type (arm64 t4g; ADR-0010)."
  type        = string
  default     = "t4g.small"

  validation {
    condition     = startswith(var.instance_type, "t4g.")
    error_message = "The hosts are arm64 t4g instances (ADR-0010)."
  }
}

variable "root_gb" {
  description = "Root volume size in GiB (OS, Docker images, swap file)."
  type        = number
  default     = 16
}

variable "data_gb" {
  description = "Data volume size in GiB (PostgreSQL, Caddy's /data, collector buffers, releases)."
  type        = number
  default     = 10
}

variable "subnet_id" {
  description = "The environment's public subnet (shared root)."
  type        = string
}

variable "hosted_zone_id" {
  description = "Route 53 zone for the hostnames (bootstrap root)."
  type        = string
}

variable "hostnames" {
  description = "Hostnames served by this host's Caddy; each gets an A record. The first is the app origin."
  type        = list(string)

  validation {
    condition     = length(var.hostnames) > 0
    error_message = "At least one hostname is required."
  }
}

variable "acme_ca" {
  description = "Caddy's ACME CA: production (default) or staging (automated tests only)."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "staging"], var.acme_ca)
    error_message = "acme_ca must be production or staging."
  }
}

variable "extra_sg_ids" {
  description = "Extra security groups for the host (for example the monitoring ingest rule, M9-09)."
  type        = list(string)
  default     = []
}
