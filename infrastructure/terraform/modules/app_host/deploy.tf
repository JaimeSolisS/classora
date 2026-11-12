# The deploy trigger (ADR-0011, plan E1 (a), #615): a custom SSM Run Command
# document whose only parameter is a 40-hex commit SHA and whose only step runs
# deploy.sh with it. The GitHub deploy role (bootstrap, #613) may send only
# this document (named classora-deploy-<env>: document names are unique per
# account and region) and only to instances tagged with its own Environment;
# it can't send AWS-RunShellScript. The document names no instance: the
# workflow targets the instance recorded in /classora/<env>/instance_id.

resource "aws_ssm_document" "deploy" {
  name            = "classora-deploy-${var.env}"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Classora ${var.env}: deploy release <sha> with /opt/classora/bin/deploy.sh (ADR-0011)."
    parameters = {
      sha = {
        type           = "String"
        description    = "The 40-hex commit SHA of the release to deploy."
        allowedPattern = "^[0-9a-f]{40}$"
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "deploy"
        precondition = {
          StringEquals = ["platformType", "Linux"]
        }
        inputs = {
          timeoutSeconds = "900"
          runCommand     = ["/opt/classora/bin/deploy.sh {{ sha }}"]
        }
      },
    ]
  })

  tags = {
    Name = "classora-deploy-${var.env}"
  }
}
