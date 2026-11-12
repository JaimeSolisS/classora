variable "instance_type" {
  description = "Monitoring host size (ADR-0014, decision 12: t4g.small)."
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
  description = "Data volume size in GiB (Prometheus, Loki, Tempo, Grafana, Caddy's /data, bundles)."
  type        = number
  default     = 20
}

variable "subnet_id" {
  description = "The monitoring subnet (shared root), in the app hosts' VPC and AZ."
  type        = string
}

variable "hosted_zone_id" {
  description = "Route 53 zone for Grafana's A record (bootstrap root)."
  type        = string
}

variable "grafana_host" {
  description = "Grafana's public hostname."
  type        = string
  default     = "grafana.classora.dev"
}

variable "domain_name" {
  description = "The SES-verified domain (alerts@<domain>)."
  type        = string
  default     = "classora.dev"
}

variable "ingest_sg_ids" {
  description = "The app hosts' security groups: the only sources admitted on the gateway's 4318."
  type        = list(string)
}

variable "alert_email" {
  description = "Alert recipient (TF_VAR_alert_email; never committed)."
  type        = string
  sensitive   = true
}

variable "retention_metrics_days" {
  description = "Prometheus retention in days."
  type        = number
  default     = 15
}

variable "retention_logs_days" {
  description = "Loki retention in days."
  type        = number
  default     = 7
}

variable "retention_traces_days" {
  description = "Tempo retention in days."
  type        = number
  default     = 7
}
