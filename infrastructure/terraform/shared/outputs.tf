output "vpc_id" {
  description = "Classora VPC."
  value       = aws_vpc.main.id
}

output "subnet_ids" {
  description = "Public subnet per host role (dev, prod, monitoring)."
  value       = { for key, subnet in aws_subnet.public : key => subnet.id }
}

output "repository_urls" {
  description = "ECR repository URLs by name."
  value       = { for name, repo in aws_ecr_repository.main : name => repo.repository_url }
}
