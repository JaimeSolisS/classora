# The hosted zone the Route 53 registrar created for the domain is adopted, never
# recreated (decision 2). Terraform adds tags only: the registrar's NS and SOA
# records are not managed here, and the zone can't be destroyed with records in it.

import {
  to = aws_route53_zone.main
  id = var.hosted_zone_id
}

resource "aws_route53_zone" "main" {
  name          = var.domain_name
  comment       = var.hosted_zone_comment
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}
