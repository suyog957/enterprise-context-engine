variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on 5432."
  type        = list(string)
}

variable "engine_version" {
  description = "PostgreSQL engine version."
  type        = string
  default     = "17.2"
}

variable "instance_class" {
  description = "Instance class (db.t4g.micro is the low-cost demo default)."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  description = "Initial storage in GiB."
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling in GiB."
  type        = number
  default     = 100
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "enterprise_context"
}

variable "master_username" {
  description = "Master username; the generated password is stored in Secrets Manager."
  type        = string
  default     = "context_admin"
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for storage encryption (null uses the AWS-managed key)."
  type        = string
  default     = null
}

variable "multi_az" {
  description = "Enable a standby in another AZ."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backup retention."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Protect against accidental deletion (and keep a final snapshot)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
