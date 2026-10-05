variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for mount targets."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to mount over NFS."
  type        = list(string)
}

variable "client_role_arns" {
  description = "Task role ARNs allowed to mount through the access point."
  type        = list(string)
}

variable "posix_uid" {
  description = "POSIX user for the access point (match the Fuseki container user)."
  type        = number
  default     = 100
}

variable "posix_gid" {
  description = "POSIX group for the access point."
  type        = number
  default     = 101
}

variable "kms_key_arn" {
  description = "KMS key (null uses the AWS-managed key)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
