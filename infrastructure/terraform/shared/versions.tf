# Shared root (ADR-0011): network and image registries used by every
# environment. Destroyed only at full teardown (`make tf-destroy ENV=shared`).
terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70.0"
    }
  }

  # Partial configuration: infrastructure/terraform/backend.hcl (git-ignored)
  # plus key=shared/terraform.tfstate from terraform.sh.
  backend "s3" {}
}

provider "aws" {
  region  = "us-east-2"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "classora"
      Environment = "shared"
      ManagedBy   = "terraform"
    }
  }
}
