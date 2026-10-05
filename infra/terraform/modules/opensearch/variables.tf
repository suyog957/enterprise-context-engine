variable "name" {
  description = "Name prefix."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "account_id" {
  description = "AWS account ID (for the domain resource ARN)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach the domain on 443."
  type        = list(string)
}

variable "master_username" {
  description = "Internal master user for fine-grained access control."
  type        = string
  default     = "context_app"
}

variable "engine_version" {
  description = "OpenSearch engine version (k-NN with the Lucene engine requires 2.x)."
  type        = string
  default     = "OpenSearch_2.17"
}

variable "instance_type" {
  description = "Data node instance type."
  type        = string
  default     = "t3.small.search"
}

variable "instance_count" {
  description = "Data node count (2+ enables zone awareness)."
  type        = number
  default     = 1
}

variable "volume_size_gb" {
  description = "EBS volume size per node."
  type        = number
  default     = 10
}

variable "kms_key_arn" {
  description = "KMS key for encryption at rest (null uses the AWS-managed key)."
  type        = string
  default     = null
}

variable "log_retention_days" {
  description = "Slow-log retention."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
