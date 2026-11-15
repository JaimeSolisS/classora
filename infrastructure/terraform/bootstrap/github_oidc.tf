# GitHub Actions deploy roles (ADR-0011, ADR-0012). One role per GitHub
# environment: `dev` (M9-07) and `production` (M9-10). Each may only push and
# read classora/* images, send the classora-deploy document to an instance tagged
# with its own Environment, read its own instance_id parameter, upload release
# bundles to its own ops bucket and describe instances. Neither role has rights
# in the other environment.
#
# The account-wide GitHub OIDC provider (one per URL per account) already exists
# and belongs to another project, so it is only read here, never created,
# imported or changed (#613 D1).

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  # Immutable subject prefix, e.g. repo:owner@123/name@456 (#613 QA D1).
  github_sub_prefix = format(
    "repo:%s@%s/%s@%s",
    split("/", var.github_repository)[0],
    var.github_owner_id,
    split("/", var.github_repository)[1],
    var.github_repository_id,
  )

  deploy_roles = {
    dev = {
      github_environment = "dev"
      env                = "dev"
    }
    prod = {
      github_environment = "production"
      env                = "prod"
    }
  }
}

data "aws_iam_policy_document" "gha_trust" {
  for_each = local.deploy_roles

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_sub_prefix}:environment:${each.value.github_environment}"]
    }
  }
}

resource "aws_iam_role" "gha" {
  for_each = local.deploy_roles

  name                 = "classora-gha-${each.key}"
  description          = "GitHub Actions ${each.value.github_environment} environment: Classora image push and ${each.value.env} deploy."
  assume_role_policy   = data.aws_iam_policy_document.gha_trust[each.key].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "gha" {
  for_each = local.deploy_roles

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrClassoraRepositories"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/classora/*"]
  }

  # SendCommand is authorized per resource: the document and every target
  # instance must each be allowed. The document is created by envs/<env>
  # (M9-06), named classora-deploy or classora-deploy-<env>.
  statement {
    sid     = "SendDeployDocument"
    actions = ["ssm:SendCommand"]
    resources = [
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:document/classora-deploy",
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:document/classora-deploy-${each.value.env}",
    ]
  }

  statement {
    sid       = "SendToOwnEnvironmentInstances"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:${local.partition}:ec2:${local.region}:${local.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["classora"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [each.value.env]
    }
  }

  statement {
    sid       = "ReadCommandResult"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadInstanceId"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/${each.value.env}/instance_id"]
  }

  statement {
    sid       = "UploadReleaseBundle"
    actions   = ["s3:PutObject"]
    resources = ["arn:${local.partition}:s3:::classora-${each.value.env}-ops-*/releases/*"]
  }

  statement {
    sid       = "DescribeInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gha" {
  for_each = local.deploy_roles

  name   = "classora-gha-${each.key}"
  role   = aws_iam_role.gha[each.key].id
  policy = data.aws_iam_policy_document.gha[each.key].json
}

# Release check (M9-10, #618): release-prod.yml's `verify` job runs BEFORE the
# owner's approval, so it can't use classora-gha-prod (that role trusts only
# jobs in the protected `production` environment, which wait for approval).
# This read-only role trusts jobs on `main` without an environment and can
# only see whether DEV tested the release (ECR tags) and whether PROD exists:
# a release is refused before anyone is asked to approve it.
data "aws_iam_policy_document" "gha_release_check_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_sub_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "gha_release_check" {
  name                 = "classora-gha-release-check"
  description          = "GitHub Actions on main (no environment): read-only checks before a PROD release."
  assume_role_policy   = data.aws_iam_policy_document.gha_release_check_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "gha_release_check" {
  statement {
    sid       = "DescribeClassoraImages"
    actions   = ["ecr:DescribeImages"]
    resources = ["arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/classora/*"]
  }

  statement {
    sid       = "ReadProdInstanceId"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/prod/instance_id"]
  }

  statement {
    sid       = "DescribeInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gha_release_check" {
  name   = "classora-gha-release-check"
  role   = aws_iam_role.gha_release_check.id
  policy = data.aws_iam_policy_document.gha_release_check.json
}
