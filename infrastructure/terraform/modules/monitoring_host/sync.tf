# The sync trigger: an SSM Run Command document with no parameters that runs
# deploy-monitoring.sh (download the bundle, `up -d`). Sent by
# `make monitoring-sync` from the operator's machine, never by CI.

resource "aws_ssm_document" "sync" {
  name            = "classora-monitoring-sync"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Classora monitoring: install the synced bundle with /opt/classora/bin/deploy-monitoring.sh (ADR-0014)."
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "sync"
        precondition = {
          StringEquals = ["platformType", "Linux"]
        }
        inputs = {
          timeoutSeconds = "900"
          runCommand     = ["/opt/classora/bin/deploy-monitoring.sh"]
        }
      },
    ]
  })

  tags = {
    Name = "classora-monitoring-sync"
  }
}
