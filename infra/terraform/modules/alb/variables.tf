variable "name" {
  description = "Name prefix (short: ALB names are limited to 32 characters)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR (egress to targets)."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the load balancer."
  type        = list(string)
}

variable "allowed_cidrs" {
  description = "Client CIDRs allowed to reach the load balancer (restrict for demos)."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN; when set, HTTP redirects to HTTPS."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Protect the load balancer from deletion."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
