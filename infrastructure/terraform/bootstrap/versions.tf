# Bootstrap root (ADR-0011): long-lived resources that survive a full teardown.
# The first apply runs on local state; `infrastructure/scripts/terraform.sh
# migrate-bootstrap` then moves the state into the bucket created here
# (_docs/runbooks/terraform.md).
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

  # Partial configuration: bucket, region and profile come from the git-ignored
  # infrastructure/terraform/backend.hcl, the key from terraform.sh.
  backend "s3" {}
}

provider "aws" {
  region  = "us-east-2"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "classora"
      Environment = "bootstrap"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
}
