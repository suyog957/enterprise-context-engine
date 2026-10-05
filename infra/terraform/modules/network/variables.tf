variable "name" {
  description = "Name prefix for network resources."
  type        = string
}

variable "region" {
  description = "AWS region (used for VPC endpoint service names)."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones (2 minimum for RDS and OpenSearch subnet groups)."
  type        = number
  default     = 2
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway (cheaper, single-AZ egress) instead of one per AZ."
  type        = bool
  default     = true
}

variable "flow_log_retention_days" {
  description = "Retention for rejected-traffic VPC flow logs; 0 disables flow logs."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
