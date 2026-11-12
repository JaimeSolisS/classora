# Monitoring root (ADR-0014; M9-09, #617): the dedicated monitoring host
# (monitoring_mode = "dedicated" in envs/dev, the default). About US$19/month;
# destroyed with `make tf-destroy ENV=monitoring` (runbook
# _docs/runbooks/monitoring.md). Telemetry is lost on destroy.
terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9.0"
    }
  }

  # Partial configuration: infrastructure/terraform/backend.hcl (git-ignored)
  # plus key=envs/monitoring/terraform.tfstate from terraform.sh.
  backend "s3" {}
}

provider "aws" {
  region  = "us-east-2"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "classora"
      Environment = "monitoring"
      ManagedBy   = "terraform"
    }
  }
}
