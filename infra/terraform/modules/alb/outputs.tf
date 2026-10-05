output "dns_name" {
  description = "Load balancer DNS name."
  value       = aws_lb.this.dns_name
}

output "security_group_id" {
  description = "Load balancer security group (grant to the web service)."
  value       = aws_security_group.this.id
}

output "web_target_group_arn" {
  description = "Target group for the web service."
  value       = aws_lb_target_group.web.arn
}
