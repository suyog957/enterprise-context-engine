output "application_url" {
  description = "Public URL of the web workspace."
  value       = "${var.certificate_arn == null ? "http" : "https"}://${module.alb.dns_name}"
}

output "ecs_cluster" {
  description = "ECS cluster name (run one-off migration/ingestion tasks here)."
  value       = aws_ecs_cluster.this.name
}

output "database_url_secret_arn" {
  description = "Secret holding DATABASE_URL (key 'url')."
  value       = module.postgres.database_url_secret_arn
}

output "opensearch_endpoint" {
  description = "VPC-only OpenSearch endpoint."
  value       = module.opensearch.endpoint
}

output "documents_bucket" {
  description = "Documents and policy bundle bucket."
  value       = module.documents.bucket_name
}

output "neptune_sparql_url" {
  description = "Optional Neptune SPARQL endpoint (unvalidated adapter)."
  value       = var.enable_neptune ? module.neptune[0].sparql_url : null
}
