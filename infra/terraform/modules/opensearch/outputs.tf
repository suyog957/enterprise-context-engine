output "endpoint" {
  description = "HTTPS endpoint of the domain (VPC-only)."
  value       = "https://${aws_opensearch_domain.this.endpoint}"
}

output "domain_arn" {
  description = "Domain ARN."
  value       = aws_opensearch_domain.this.arn
}

output "security_group_id" {
  description = "Domain security group."
  value       = aws_security_group.this.id
}

output "connection_secret_arn" {
  description = "Secrets Manager ARN whose 'url' key is the authenticated OPENSEARCH_URL."
  value       = aws_secretsmanager_secret.master.arn
}
