output "endpoint" {
  description = "Host:port of the PostgreSQL instance."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Hostname of the PostgreSQL instance."
  value       = aws_db_instance.this.address
}

output "database_name" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN whose 'url' key is the DATABASE_URL."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "security_group_id" {
  description = "Database security group."
  value       = aws_security_group.this.id
}
