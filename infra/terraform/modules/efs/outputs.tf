output "file_system_id" {
  description = "EFS file system ID."
  value       = aws_efs_file_system.this.id
}

output "access_point_id" {
  description = "Access point for the Fuseki data directory."
  value       = aws_efs_access_point.this.id
}
