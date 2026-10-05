variable "name" {
  description = "Service and container name."
  type        = string
}

variable "region" {
  description = "AWS region (for log configuration)."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for tasks."
  type        = list(string)
}

variable "image" {
  description = "Container image (immutable tag or digest recommended)."
  type        = string
}

variable "command" {
  description = "Optional container command override."
  type        = list(string)
  default     = null
}

variable "container_port" {
  description = "Container port (null for workers without a listener)."
  type        = number
  default     = null
}

variable "cpu" {
  description = "Task CPU units."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Task memory (MiB)."
  type        = number
  default     = 1024
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64."
  type        = string
  default     = "X86_64"
}

variable "desired_count" {
  description = "Number of tasks."
  type        = number
  default     = 1
}

variable "environment" {
  description = "Plain environment variables (no secrets)."
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "Environment variable name -> Secrets Manager ARN (optionally with a JSON key)."
  type        = map(string)
  default     = {}
}

variable "sidecars" {
  description = "Additional container definitions (e.g. OPA) as maps in ECS JSON shape."
  type        = list(any)
  default     = []
}

variable "task_policy_json" {
  description = "IAM policy JSON for the task role (least privilege); null for none."
  type        = string
  default     = null
}

variable "ingress_security_group_ids" {
  description = "Security groups allowed to reach the container port."
  type        = list(string)
  default     = []
}

variable "additional_security_group_ids" {
  description = "Extra security groups attached to tasks."
  type        = list(string)
  default     = []
}

variable "target_group_arn" {
  description = "Load balancer target group (null if not exposed through the ALB)."
  type        = string
  default     = null
}

variable "service_discovery_namespace_id" {
  description = "Cloud Map private namespace for internal DNS (null to skip)."
  type        = string
  default     = null
}

variable "discovery_name" {
  description = "DNS label within the namespace (defaults to the service name)."
  type        = string
  default     = null
}

variable "health_check_command" {
  description = "Container health check command."
  type        = list(string)
  default     = null
}

variable "readonly_root_filesystem" {
  description = "Mount the container root filesystem read-only."
  type        = bool
  default     = false
}

variable "efs_volume" {
  description = "Optional EFS volume mounted for persistent state (Fuseki TDB2)."
  type = object({
    file_system_id  = string
    access_point_id = string
    container_path  = string
  })
  default = null
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
