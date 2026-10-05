output "endpoint" {
  description = "Writer endpoint."
  value       = aws_neptune_cluster.this.endpoint
}

output "sparql_url" {
  description = "SPARQL endpoint URL (requires a SigV4-signing adapter)."
  value       = "https://${aws_neptune_cluster.this.endpoint}:8182/sparql"
}
