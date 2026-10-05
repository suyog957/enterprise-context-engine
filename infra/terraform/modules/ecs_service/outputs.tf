output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.this.name
}

output "security_group_id" {
  description = "Task security group (grant this access to data stores)."
  value       = aws_security_group.this.id
}

output "task_role_arn" {
  description = "Task role ARN."
  value       = aws_iam_role.task.arn
}

output "task_role_name" {
  description = "Task role name (attach extra policies, e.g. Bedrock or EFS)."
  value       = aws_iam_role.task.name
}

output "log_group_name" {
  description = "CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}
