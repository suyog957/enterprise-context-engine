variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "dev"
}

variable "name" {
  description = "Short name prefix for resources."
  type        = string
  default     = "ecg-dev"
}

variable "allowed_cidrs" {
  description = "Client CIDRs allowed to reach the public load balancer. Restrict this."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS (recommended)."
  type        = string
  default     = null
}

variable "api_image" {
  description = "API image (runtime target of apps/api/Dockerfile)."
  type        = string
}

variable "web_image" {
  description = "Web image (apps/web/Dockerfile)."
  type        = string
}

variable "opa_image" {
  description = "OPA image with the policy bundle (infra/docker/opa.Dockerfile)."
  type        = string
}

variable "fuseki_image" {
  description = "Apache Jena Fuseki image."
  type        = string
  default     = "stain/jena-fuseki:5.1.0"
}

variable "documents_bucket_name" {
  description = "Globally unique S3 bucket name for documents and policy bundles."
  type        = string
}

variable "enable_neptune" {
  description = "Create the optional (unvalidated) Neptune cluster."
  type        = bool
  default     = false
}

variable "bedrock_model_ids" {
  description = "Bedrock foundation models the API may invoke (empty disables Bedrock access)."
  type        = list(string)
  default     = []
}

variable "llm_provider" {
  description = "mock (default), openai_compatible or bedrock."
  type        = string
  default     = "mock"
}

variable "llm_model" {
  description = "Model identifier when llm_provider is not mock."
  type        = string
  default     = ""
}

variable "otel_exporter_otlp_endpoint" {
  description = "OTLP/HTTP collector endpoint (e.g. an ADOT collector); empty disables tracing export."
  type        = string
  default     = ""
}

variable "dry_run" {
  description = "Keep purchase-order creation in dry-run mode."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Protect stateful resources (RDS, ALB) from deletion."
  type        = bool
  default     = true
}

variable "allow_dev_identity" {
  description = "PRIVATE DEMO ONLY: accept the unauthenticated X-Dev-Principal header (sets ENVIRONMENT=local). Requires tightly restricted allowed_cidrs."
  type        = bool
  default     = false
}
