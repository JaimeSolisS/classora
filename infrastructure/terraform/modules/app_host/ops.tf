# Ops bucket (ADR-0011): release bundles under releases/ (written by the
# deploy workflow, read by the host) and the nightly backups under backups/
# (backup.sh, kept 14 days). Destroyed with the environment.

resource "random_id" "ops_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "ops" {
  bucket        = "${local.name}-ops-${random_id.ops_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "ops" {
  bucket = aws_s3_bucket.ops.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "ops" {
  bucket = aws_s3_bucket.ops.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ops" {
  bucket = aws_s3_bucket.ops.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "ops" {
  bucket = aws_s3_bucket.ops.id

  rule {
    id     = "releases"
    status = "Enabled"

    filter {
      prefix = "releases/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Nightly dumps (ADR-0013): 14 days. The bucket isn't versioned; the
  # noncurrent rule only matters if versioning is ever turned on.
  rule {
    id     = "backups-14d"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    expiration {
      days = 14
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

data "aws_iam_policy_document" "ops" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.ops.arn,
      "${aws_s3_bucket.ops.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "ops" {
  bucket = aws_s3_bucket.ops.id
  policy = data.aws_iam_policy_document.ops.json

  depends_on = [aws_s3_bucket_public_access_block.ops]
}
