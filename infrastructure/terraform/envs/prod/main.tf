# The bootstrap (hosted zone, archive bucket) and shared (subnet) outputs,
# read from their states in the same bucket.
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

# The same module as DEV (#614…#616, #632): t4g.small (ADR-0010, decision 12;
# no colocated monitoring on PROD), its own encrypted data volume, Elastic IP,
# Caddy HTTPS for app.classora.dev, nightly dumps and daily snapshots, the
# deploy document classora-deploy-prod, and Put/Get on the archive bucket's
# prod/ prefix for the final dump (ADR-0013). Telemetry goes to the dedicated
# monitoring host when /classora/shared/otlp_endpoint exists; re-apply
# envs/monitoring after this root so its 4318 rule admits this host's group.
module "app_host" {
  source = "../../modules/app_host"

  env            = "prod"
  instance_type  = "t4g.small"
  subnet_id      = data.terraform_remote_state.shared.outputs.subnet_ids["prod"]
  hosted_zone_id = data.terraform_remote_state.bootstrap.outputs.hosted_zone_id
  hostnames      = ["app.classora.dev"]
  acme_ca        = var.acme_ca
  archive_bucket = data.terraform_remote_state.bootstrap.outputs.archive_bucket
}
