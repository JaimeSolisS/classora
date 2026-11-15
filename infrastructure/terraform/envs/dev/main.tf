# The bootstrap (hosted zone) and shared (subnet) outputs, read from their
# states in the same bucket.
data "terraform_remote_state" "bootstrap" {
  backend = "s3"

  config = {
    bucket  = var.state_bucket
    key     = "bootstrap/terraform.tfstate"
    region  = "us-east-2"
    profile = var.aws_profile
  }
}

data "terraform_remote_state" "shared" {
  backend = "s3"

  config = {
    bucket  = var.state_bucket
    key     = "shared/terraform.tfstate"
    region  = "us-east-2"
    profile = var.aws_profile
  }
}

locals {
  # Colocated monitoring (ADR-0014, decision 12): DEV grows to a t4g.medium
  # and a 30 GB data volume (both in place: a 1-2 minute stop for the resize).
  colocated     = var.monitoring_mode == "colocated"
  instance_type = local.colocated ? "t4g.medium" : var.instance_type
  data_gb       = local.colocated ? 30 : 10
}

module "app_host" {
  source = "../../modules/app_host"

  env            = "dev"
  instance_type  = local.instance_type
  data_gb        = local.data_gb
  subnet_id      = data.terraform_remote_state.shared.outputs.subnet_ids["dev"]
  hosted_zone_id = data.terraform_remote_state.bootstrap.outputs.hosted_zone_id
  hostnames      = ["dev.classora.dev"]
  acme_ca        = var.acme_ca
  archive_bucket = data.terraform_remote_state.bootstrap.outputs.archive_bucket
  # DEV never archives real data: its final-dump drill writes here (#616).
  archive_prefix = "dev-drill"
}

# --- Monitoring placement (ADR-0014; M9-09, #617) ---------------------------

# Read by render-env.sh: deploy.sh starts the monitoring stack on this host
# only when colocated.
resource "aws_ssm_parameter" "monitoring_mode" {
  name        = "/classora/dev/monitoring_mode"
  description = "Classora dev: where the monitoring stack runs (dedicated, colocated or off)."
  type        = "String"
  value       = var.monitoring_mode
}

# Colocated: the stack's settings under /classora/dev/monitoring/, the SMTP
# user, the shared endpoint (the gateway on DEV's app network) and Grafana's
# record on DEV's address.
module "monitoring" {
  source = "../../modules/monitoring_config"
  count  = local.colocated ? 1 : 0

  parameter_prefix = "/classora/dev/monitoring"
  domain_name      = data.terraform_remote_state.bootstrap.outputs.ses_domain_identity
  alert_email      = var.alert_email
  otlp_endpoint    = "http://otel-collector:4318"
}

resource "aws_route53_record" "grafana" {
  count = local.colocated ? 1 : 0

  zone_id = data.terraform_remote_state.bootstrap.outputs.hosted_zone_id
  name    = "grafana.classora.dev"
  type    = "A"
  ttl     = 300
  records = [module.app_host.public_ip]
}

# The dedicated host (envs/monitoring) owns the same names: colocated and off
# need it destroyed first.
data "aws_instances" "monitoring" {
  instance_tags = {
    Project     = "classora"
    Environment = "monitoring"
  }
  instance_state_names = ["pending", "running", "stopping", "stopped"]
}

resource "terraform_data" "monitoring_mode_guard" {
  input = var.monitoring_mode

  lifecycle {
    precondition {
      condition     = var.monitoring_mode == "dedicated" || length(data.aws_instances.monitoring.ids) == 0
      error_message = "The dedicated monitoring host exists: run make tf-destroy ENV=monitoring before monitoring_mode = \"colocated\" or \"off\" (runbook _docs/runbooks/monitoring.md)."
    }
    precondition {
      condition     = !local.colocated || var.alert_email != null
      error_message = "monitoring_mode = \"colocated\" needs TF_VAR_alert_email."
    }
  }
}
