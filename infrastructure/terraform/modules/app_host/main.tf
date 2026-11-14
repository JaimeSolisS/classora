# One app host (ADR-0010): Amazon Linux 2023 arm64 on t4g, an Elastic IP, a
# security group with only 80/443, Session Manager (no SSH), and a separate
# encrypted data volume that survives instance replacement. Cloud-init
# installs Docker and Compose, mounts the data volume and enables the systemd
# units; releases are deployed by /opt/classora/bin/deploy.sh (ADR-0011).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_subnet" "host" {
  id = var.subnet_id
}

# Latest AL2023 arm64 at the time the instance is created. Ignored afterwards
# (lifecycle below): a new AMI never replaces the host; replacement is a
# deliberate `terraform apply -replace`.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  name       = "classora-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
  az         = data.aws_subnet.host.availability_zone
  host_dir   = "${path.module}/../../../host"

  archive_prefix = coalesce(var.archive_prefix, var.env)
}

resource "aws_security_group" "host" {
  name        = "${local.name}-host"
  description = "Classora ${var.env} host: HTTP and HTTPS only (ADR-0010)."
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

# The host's network interface and Elastic IP exist before the instance, so
# the instance has its public address from first boot (the subnets don't
# assign public IPs and there is no NAT): cloud-init can reach dnf, GitHub
# and SSM straight away. A replaced instance gets the same interface, private
# address and Elastic IP.
resource "aws_network_interface" "host" {
  subnet_id       = var.subnet_id
  security_groups = concat([aws_security_group.host.id], var.extra_sg_ids)
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

  # Hop limit 1: only the host itself reaches IMDS (the AWS CLI in the host
  # scripts, the SSM agent, dockerd's ECR pulls through ecr-login.sh). A
  # container is one hop further and gets no token, so code running in api or
  # worker can't use the instance role (Parameter Store secrets, the ops
  # bucket). No container needs instance credentials (#632).
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
      Environment = var.env
      ManagedBy   = "terraform"
      Role        = "root"
      App         = "classora-${var.env}"
    }
  }

  tags = {
    Name = local.name
  }

  lifecycle {
    # A new AMI or a changed cloud-init never replaces the host: user data runs
    # only at first boot, and deploy.sh refreshes the host scripts from each
    # release bundle. Replacement is `terraform apply -replace` (runbook).
    ignore_changes = [ami, user_data_base64]

    # EC2 rejects more than 16 KiB of user data (before base64), and only at
    # launch, long after a plan: fail the plan instead. 21848 base64
    # characters are 16384 bytes.
    precondition {
      condition     = length(base64gzip(local.cloud_init)) <= 21848
      error_message = "The gzipped cloud-init is over EC2's 16 KiB user data limit; ship more host scripts only in the release bundle (cloud_init.tf)."
    }
  }

  # The EIP is attached to the interface, so it must exist before boot.
  depends_on = [aws_eip.host]
}

# The data volume: PostgreSQL, Caddy's /data (certificates), collector buffers
# and the deployed releases. No prevent_destroy (ADR-0011: destroying an
# environment is normal; PROD is guarded by the M9-10 final-dump check).
# Role=data selects it for the M9-08 snapshot policy.
resource "aws_ebs_volume" "data" {
  availability_zone = local.az
  type              = "gp3"
  size              = var.data_gb
  encrypted         = true

  tags = {
    Name = "${local.name}-data"
    Role = "data"
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.this.id

  # On a replacement or destroy the instance is stopped first (systemd stops
  # the stacks, PostgreSQL shuts down cleanly), then the volume is detached.
  stop_instance_before_detaching = true
}

resource "aws_route53_record" "host" {
  for_each = toset(var.hostnames)

  zone_id = var.hosted_zone_id
  name    = each.value
  type    = "A"
  ttl     = 300
  records = [aws_eip.host.public_ip]
}
