variable "parameter_prefix" {
  description = "Parameter Store path for the stack's settings: /classora/monitoring (dedicated host) or /classora/<env>/monitoring (colocated)."
  type        = string

  validation {
    condition     = can(regex("^/classora/(monitoring|(dev|prod)/monitoring)$", var.parameter_prefix))
    error_message = "parameter_prefix is /classora/monitoring or /classora/<env>/monitoring."
  }
}

variable "domain_name" {
  description = "The SES-verified domain (bootstrap root); alerts are sent from alerts@<domain>."
  type        = string
  default     = "classora.dev"
}

variable "grafana_host" {
  description = "Grafana's public hostname (Caddy, automatic HTTPS)."
  type        = string
  default     = "grafana.classora.dev"
}

variable "alert_email" {
  description = "Alert recipient (a verified SES identity while the account is in the sandbox). Never committed: TF_VAR_alert_email."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[a-z]+$", var.alert_email))
    error_message = "alert_email must be an e-mail address (export TF_VAR_alert_email)."
  }
}

variable "otlp_endpoint" {
  description = "The gateway endpoint the app hosts' agents export to, published as /classora/shared/otlp_endpoint."
  type        = string
}

variable "dedicated" {
  description = "True on the dedicated monitoring host (then ingest_address is required), false when colocated."
  type        = bool
  default     = false
}

variable "ingest_address" {
  description = "Dedicated host only: the private IP the gateway publishes 4318 on."
  type        = string
  default     = null
}

variable "retention_metrics_days" {
  description = "Prometheus retention in days (ADR-0014: 15)."
  type        = number
  default     = 15
}

variable "retention_logs_days" {
  description = "Loki retention in days (ADR-0014: 7)."
  type        = number
  default     = 7
}

variable "retention_traces_days" {
  description = "Tempo retention in days (ADR-0014: 7)."
  type        = number
  default     = 7
}

variable "extra_parameters" {
  description = "More String parameters under the prefix (for example the dedicated host's instance_id and ops_bucket)."
  type        = map(string)
  default     = {}
}
