#!/usr/bin/env bash
# Log Docker in to this account's ECR with the instance role (ADR-0011, #614).
# The token (12 hours) goes to root's Docker config only, never to output.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${AWS_REGION:?}"

account=$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)
aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$account.dkr.ecr.$AWS_REGION.amazonaws.com" >/dev/null
echo "ecr-login: logged in"
