# Amazon SES in us-east-2 for alert e-mail (decision 7, ADR-0014): the domain
# identity with Easy DKIM (three CNAMEs in the zone), a monitoring-only DMARC
# record, and the owner's address as a verified recipient while the account is
# in the SES sandbox. The SMTP credentials come with M9-09 (#617).

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.domain_name

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

resource "aws_route53_record" "dkim" {
  count = 3

  zone_id = aws_route53_zone.main.zone_id
  name    = "${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 1800
  records = ["${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_route53_record" "dmarc" {
  zone_id = aws_route53_zone.main.zone_id
  name    = "_dmarc.${var.domain_name}"
  type    = "TXT"
  ttl     = 1800
  records = ["v=DMARC1; p=none"]
}

resource "aws_sesv2_email_identity" "alert_recipient" {
  email_identity = var.alert_email
}
