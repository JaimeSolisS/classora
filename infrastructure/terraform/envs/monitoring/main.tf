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

# The app hosts that exist now (classora-<env>-host, modules/app_host): the
# only sources admitted on the gateway's 4318. A re-created DEV, or PROD
# (M9-10), gets a new group: re-apply this root (runbook).
data "aws_security_groups" "app_hosts" {
  filter {
    name   = "group-name"
    values = ["classora-dev-host", "classora-prod-host"]
  }

  filter {
    name   = "vpc-id"
    values = [data.terraform_remote_state.shared.outputs.vpc_id]
  }
}

# Exclusive with a colocated stack: an app root with monitoring_mode =
# "colocated" owns /classora/<env>/monitoring/*, the SMTP user, the shared
# endpoint and the grafana record. Switch by destroying this root first.
data "aws_ssm_parameters_by_path" "colocated" {
  for_each = toset(["dev", "prod"])

  path            = "/classora/${each.key}/monitoring"
  with_decryption = false
}

module "monitoring_host" {
  source = "../../modules/monitoring_host"

  subnet_id              = data.terraform_remote_state.shared.outputs.subnet_ids["monitoring"]
  hosted_zone_id         = data.terraform_remote_state.bootstrap.outputs.hosted_zone_id
  domain_name            = data.terraform_remote_state.bootstrap.outputs.ses_domain_identity
  ingest_sg_ids          = sort(data.aws_security_groups.app_hosts.ids)
  alert_email            = var.alert_email
  retention_metrics_days = var.retention_metrics_days
  retention_logs_days    = var.retention_logs_days
  retention_traces_days  = var.retention_traces_days
}

resource "terraform_data" "mode_guard" {
  lifecycle {
    precondition {
      condition     = alltrue([for env, p in data.aws_ssm_parameters_by_path.colocated : length(p.names) == 0])
      error_message = "Monitoring is colocated on an app host (parameters under /classora/<env>/monitoring exist): set monitoring_mode back to \"dedicated\" in that root and apply it first (runbook _docs/runbooks/monitoring.md)."
    }
  }
}
