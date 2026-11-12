# Daily snapshots of the data volume (ADR-0013, #616): Amazon Data Lifecycle
# Manager at 09:00 UTC (after the 08:30 dump), 7 kept, the volume's tags
# copied (Project, Environment, Role). Crash-consistent; the nightly dump
# (backup.sh) is the primary recovery path. Restore drill:
# _docs/runbooks/restore.md.
#
# DLM targets a volume that carries ANY of the target tags, so the policy
# names one tag only this environment's data volume has: its Name
# (classora-<env>-data). Project/Environment/Role would also match the root
# volume and the other environment's volumes.

data "aws_iam_policy_document" "dlm_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

# Per environment rather than the account-wide AWSDataLifecycleManagerDefaultRole:
# created and destroyed with the environment, nothing left in the account.
resource "aws_iam_role" "dlm" {
  name               = "${local.name}-dlm"
  description        = "Classora ${var.env}: Data Lifecycle Manager snapshots of the data volume."
  assume_role_policy = data.aws_iam_policy_document.dlm_trust.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  # DLM descriptions allow only letters, digits, spaces, dashes and underscores.
  description        = "Classora ${var.env} data volume daily snapshot 0900 UTC keep 7"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    policy_type    = "EBS_SNAPSHOT_MANAGEMENT"
    resource_types = ["VOLUME"]

    target_tags = {
      Name = aws_ebs_volume.data.tags["Name"]
    }

    schedule {
      name      = "daily-0900-keep-7"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["09:00"]
      }

      retain_rule {
        count = 7
      }

      tags_to_add = {
        SnapshotCreator = "dlm"
      }
    }
  }

  tags = {
    Name = "${local.name}-data-snapshots"
  }

  depends_on = [aws_iam_role_policy_attachment.dlm]
}
