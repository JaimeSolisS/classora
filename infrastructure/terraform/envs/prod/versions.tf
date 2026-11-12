# PROD environment root (ADR-0010, ADR-0011, #618): one app host running the
# data and app stacks under systemd, at app.classora.dev. About US$18/month;
# destroyed only after a verified final dump: `make env-teardown` or
# `make tf-destroy ENV=prod` (runbook _docs/runbooks/teardown.md).
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
  # plus key=envs/prod/terraform.tfstate from terraform.sh.
  backend "s3" {}
}

provider "aws" {
  region  = "us-east-2"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "classora"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}
