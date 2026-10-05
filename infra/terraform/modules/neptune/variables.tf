variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach Neptune on 8182."
  type        = list(string)
}

variable "engine_version" {
  description = "Neptune engine version."
  type        = string
  default     = "1.3.2.1"
}

variable "instance_class" {
  description = "Instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "instance_count" {
  description = "Number of instances (1 writer)."
  type        = number
  default     = 1
}

variable "kms_key_arn" {
  description = "KMS key (null uses the AWS-managed key)."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Protect against deletion."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
