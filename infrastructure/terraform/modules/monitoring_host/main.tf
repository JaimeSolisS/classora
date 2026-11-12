# The dedicated monitoring host (ADR-0014; M9-09, #617): Amazon Linux 2023
# arm64 on a t4g.small, an Elastic IP and Grafana's A record, Session Manager
# (no SSH), and an encrypted data volume for Prometheus, Loki, Tempo, Grafana
# and Caddy. The security group admits 80/443 from anywhere (Caddy, Grafana's
# login) and the gateway's 4318 only from the app hosts' security groups.
# The stack comes from a bundle synced by `make monitoring-sync`
# (deploy-monitoring.sh); the same files as the local profile.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_subnet" "host" {
  id = var.subnet_id
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  name       = "classora-monitoring"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
  az         = data.aws_subnet.host.availability_zone
}

resource "aws_security_group" "host" {
  name        = "${local.name}-host"
  description = "Classora monitoring host: HTTP/HTTPS, and OTLP 4318 from the app hosts only (ADR-0014)."
  vpc_id      = data.aws_subnet.host.vpc_id

  tags = {
    Name = "${local.name}-host"
  }
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = {
    http-ipv4  = { port = 80, cidr_ipv4 = "0.0.0.0/0", cidr_ipv6 = null }
    https-ipv4 = { port = 443, cidr_ipv4 = "0.0.0.0/0", cidr_ipv6 = null }
    http-ipv6  = { port = 80, cidr_ipv4 = null, cidr_ipv6 = "::/0" }
    https-ipv6 = { port = 443, cidr_ipv4 = null, cidr_ipv6 = "::/0" }
  }

  security_group_id = aws_security_group.host.id
  description       = each.key
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr_ipv4
  cidr_ipv6         = each.value.cidr_ipv6
}

# OTLP/HTTP from each app host's security group, nothing else (ADR-0014
# "Ingest security"; in-VPC, same AZ, not TLS).
resource "aws_vpc_security_group_ingress_rule" "otlp" {
  for_each = toset(var.ingest_sg_ids)

  security_group_id            = aws_security_group.host.id
  description                  = "otlp-from-${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = 4318
  to_port                      = 4318
  referenced_security_group_id = each.key
}

resource "aws_vpc_security_group_egress_rule" "all" {
  for_each = {
    ipv4 = { cidr_ipv4 = "0.0.0.0/0", cidr_ipv6 = null }
    ipv6 = { cidr_ipv4 = null, cidr_ipv6 = "::/0" }
  }

  security_group_id = aws_security_group.host.id
  description       = "all-${each.key}"
  ip_protocol       = "-1"
  cidr_ipv4         = each.value.cidr_ipv4
  cidr_ipv6         = each.value.cidr_ipv6
}

# Interface and Elastic IP before the instance (as for the app hosts, #614
# D2): internet from first boot, and the same private IP (the agents'
# endpoint) and public IP after a replacement.
resource "aws_network_interface" "host" {
  subnet_id       = var.subnet_id
  security_groups = [aws_security_group.host.id]
  description     = "${local.name} host"

  tags = {
    Name = local.name
  }
}

resource "aws_eip" "host" {
  domain            = "vpc"
  network_interface = aws_network_interface.host.id

  tags = {
    Name = local.name
  }
}

resource "aws_instance" "this" {
  ami                  = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type        = var.instance_type
  iam_instance_profile = aws_iam_instance_profile.host.name
  monitoring           = false
  user_data_base64     = base64gzip(local.cloud_init)

  primary_network_interface {
    network_interface_id = aws_network_interface.host.id
  }

  # The containers need no instance credentials: hop limit 1 keeps IMDS to
  # the host's own scripts (render-monitoring-env.sh, the AWS CLI).
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_gb
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name        = "${local.name}-root"
      Project     = "classora"
      Environment = "monitoring"
      ManagedBy   = "terraform"
      Role        = "root"
    }
  }

  tags = {
    Name = local.name
  }

  lifecycle {
    # User data runs only at first boot; deploy-monitoring.sh refreshes the
    # scripts from each bundle. Replacement is `terraform apply -replace`.
    ignore_changes = [ami, user_data_base64]

    precondition {
      condition     = length(base64gzip(local.cloud_init)) <= 21848
      error_message = "The gzipped cloud-init is over EC2's 16 KiB user data limit."
    }
  }

  depends_on = [aws_eip.host]
}

# Telemetry stores (metrics 15 days, logs and traces 7), Grafana's database
# and Caddy's certificates. Lost on destroy (ADR-0014: accepted). Not
# snapshotted (no Role=data, no DLM policy targets it).
resource "aws_ebs_volume" "data" {
  availability_zone = local.az
  type              = "gp3"
  size              = var.data_gb
  encrypted         = true

  tags = {
    Name = "${local.name}-data"
    Role = "monitoring-data"
  }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.this.id
  stop_instance_before_detaching = true
}

resource "aws_route53_record" "grafana" {
  zone_id = var.hosted_zone_id
  name    = var.grafana_host
  type    = "A"
  ttl     = 300
  records = [aws_eip.host.public_ip]
}

module "config" {
  source = "../monitoring_config"

  parameter_prefix       = "/classora/monitoring"
  domain_name            = var.domain_name
  grafana_host           = var.grafana_host
  alert_email            = var.alert_email
  otlp_endpoint          = "http://${aws_network_interface.host.private_ip}:4318"
  dedicated              = true
  ingest_address         = aws_network_interface.host.private_ip
  retention_metrics_days = var.retention_metrics_days
  retention_logs_days    = var.retention_logs_days
  retention_traces_days  = var.retention_traces_days
  extra_parameters = {
    # For make monitoring-sync (no Terraform needed on the operator's side).
    instance_id = aws_instance.this.id
    ops_bucket  = aws_s3_bucket.ops.bucket
  }
}
