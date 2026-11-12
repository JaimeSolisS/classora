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

module "app_host" {
  source = "../../modules/app_host"

  env            = "dev"
  instance_type  = var.instance_type
  subnet_id      = data.terraform_remote_state.shared.outputs.subnet_ids["dev"]
  hosted_zone_id = data.terraform_remote_state.bootstrap.outputs.hosted_zone_id
  hostnames      = ["dev.classora.dev"]
  acme_ca        = var.acme_ca
  archive_bucket = data.terraform_remote_state.bootstrap.outputs.archive_bucket
  # DEV never archives real data: its final-dump drill writes here (#616).
  archive_prefix = "dev-drill"
}
