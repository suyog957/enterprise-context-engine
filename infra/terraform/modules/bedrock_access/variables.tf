variable "role_name" {
  description = "IAM role (the API task role) that may invoke the models."
  type        = string
}

variable "region" {
  description = "Bedrock region."
  type        = string
}

variable "model_ids" {
  description = "Foundation model IDs the application may invoke."
  type        = list(string)
}
