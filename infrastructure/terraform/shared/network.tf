# One VPC, one public subnet per host, one Availability Zone, an internet
# gateway and no NAT gateway or endpoints (ADR-0010). Hosts get Elastic IPs
# (M9-06), so subnets don't assign public IPs.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "classora"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "classora"
  }
}

resource "aws_subnet" "public" {
  for_each = var.subnets

  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value
  availability_zone       = var.az
  map_public_ip_on_launch = false

  tags = {
    Name = "classora-${each.key}"
    Role = each.key
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "classora-public"
  }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}
