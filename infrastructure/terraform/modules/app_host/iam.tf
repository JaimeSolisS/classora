# Instance role (ADR-0012): Session Manager, its own Parameter Store path and
# /classora/shared/, decryption only through SSM, ECR pulls of classora/*,
# release bundles from its own ops bucket, backups into and out of it, and its
# own prefix of the archive bucket (ADR-0013).

data "aws_iam_policy_document" "host_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  description        = "Classora ${var.env} host: SSM, own parameters, ECR pull, own ops bucket."
  assume_role_policy = data.aws_iam_policy_document.host_trust.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid     = "ReadOwnAndSharedParameters"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/${var.env}",
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/${var.env}/*",
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/shared",
      "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter/classora/shared/*",
    ]
  }

  statement {
    sid       = "DecryptThroughSsmOnly"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPullClassora"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = ["arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/classora/*"]
  }

  statement {
    sid       = "ReadReleaseBundles"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.ops.arn}/releases/*"]
  }

  # Nightly dumps (backup.sh) and the restore drill (restore.sh latest). No
  # delete: a failed dump never completes its upload, and the lifecycle rule
  # expires old dumps.
  statement {
    sid       = "WriteAndReadBackups"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.ops.arn}/backups/*"]
  }

  statement {
    sid       = "ListBackups"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.ops.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["backups/", "backups/*"]
    }
  }

  # The final dump at shutdown (final-dump.sh, ADR-0013): only this
  # environment's prefix of the archive bucket; no list, no delete.
  statement {
    sid       = "WriteAndReadOwnArchivePrefix"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:${local.partition}:s3:::${var.archive_bucket}/${local.archive_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${local.name}-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
}
