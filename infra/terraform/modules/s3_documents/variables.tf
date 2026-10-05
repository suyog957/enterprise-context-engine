variable "bucket_name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for SSE-KMS (null uses SSE-S3)."
  type        = string
  default     = null
}

variable "noncurrent_version_retention_days" {
  description = "How long superseded document versions are kept."
  type        = number
  default     = 90
}

variable "force_destroy" {
  description = "Allow destroying a non-empty bucket (demo environments only)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
