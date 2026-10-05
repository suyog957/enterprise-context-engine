data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  namespace  = "ecg.internal"
  # Without an OIDC provider the API only accepts the local X-Dev-Principal header,
  # which must never face the internet. allow_dev_identity opts into it explicitly for
  # a private demo restricted by allowed_cidrs; otherwise every request is refused.
  api_environment = var.allow_dev_identity ? "local" : var.environment
}

module "network" {
  source = "../../modules/network"
  name   = var.name
  region = var.region
}

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = local.namespace
  description = "Private service discovery for ${var.name}"
  vpc         = module.network.vpc_id
}

module "documents" {
  source      = "../../modules/s3_documents"
  bucket_name = var.documents_bucket_name
}

resource "random_password" "fuseki_admin" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "fuseki_admin" {
  name                    = "${var.name}/fuseki/admin-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "fuseki_admin" {
  secret_id     = aws_secretsmanager_secret.fuseki_admin.id
  secret_string = random_password.fuseki_admin.result
}

module "alb" {
  source              = "../../modules/alb"
  name                = var.name
  vpc_id              = module.network.vpc_id
  vpc_cidr_block      = module.network.vpc_cidr_block
  public_subnet_ids   = module.network.public_subnet_ids
  allowed_cidrs       = var.allowed_cidrs
  certificate_arn     = var.certificate_arn
  deletion_protection = var.deletion_protection
}

module "postgres" {
  source                     = "../../modules/rds_postgres"
  name                       = var.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.api.security_group_id, module.projector.security_group_id]
  deletion_protection        = var.deletion_protection
}

module "opensearch" {
  source                     = "../../modules/opensearch"
  name                       = var.name
  region                     = var.region
  account_id                 = local.account_id
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.api.security_group_id]
}

module "fuseki_storage" {
  source                     = "../../modules/efs"
  name                       = "${var.name}-fuseki"
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.fuseki.security_group_id]
  client_role_arns           = [module.fuseki.task_role_arn]
}

module "fuseki" {
  source                         = "../../modules/ecs_service"
  name                           = "${var.name}-fuseki"
  discovery_name                 = "fuseki"
  region                         = var.region
  cluster_arn                    = aws_ecs_cluster.this.arn
  vpc_id                         = module.network.vpc_id
  subnet_ids                     = module.network.private_subnet_ids
  image                          = var.fuseki_image
  container_port                 = 3030
  cpu                            = 1024
  memory                         = 3072
  environment                    = { FUSEKI_DATASET_1 = "enterprise", JVM_ARGS = "-Xmx2g" }
  secret_arns                    = { ADMIN_PASSWORD = aws_secretsmanager_secret.fuseki_admin.arn }
  ingress_security_group_ids     = [module.api.security_group_id, module.projector.security_group_id]
  service_discovery_namespace_id = aws_service_discovery_private_dns_namespace.this.id
  efs_volume = {
    file_system_id  = module.fuseki_storage.file_system_id
    access_point_id = module.fuseki_storage.access_point_id
    container_path  = "/fuseki"
  }
}

locals {
  application_environment = {
    ENVIRONMENT                 = local.api_environment
    FUSEKI_URL                  = "http://fuseki.${local.namespace}:3030/enterprise"
    OPA_URL                     = "http://127.0.0.1:8181"
    DRY_RUN                     = tostring(var.dry_run)
    LLM_PROVIDER                = var.llm_provider
    LLM_MODEL                   = var.llm_model
    AWS_REGION                  = var.region
    OTEL_EXPORTER_OTLP_ENDPOINT = var.otel_exporter_otlp_endpoint
  }
}

module "api" {
  source                         = "../../modules/ecs_service"
  name                           = "${var.name}-api"
  discovery_name                 = "api"
  region                         = var.region
  cluster_arn                    = aws_ecs_cluster.this.arn
  vpc_id                         = module.network.vpc_id
  subnet_ids                     = module.network.private_subnet_ids
  image                          = var.api_image
  container_port                 = 8000
  cpu                            = 1024
  memory                         = 2048
  environment                    = local.application_environment
  ingress_security_group_ids     = [module.web.security_group_id]
  service_discovery_namespace_id = aws_service_discovery_private_dns_namespace.this.id
  health_check_command           = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready', timeout=2)\""]
  secret_arns = {
    DATABASE_URL   = "${module.postgres.database_url_secret_arn}:url::"
    OPENSEARCH_URL = "${module.opensearch.connection_secret_arn}:url::"
  }
  # OPA runs as a sidecar on loopback; policies are baked into the image.
  sidecars = [{
    name      = "opa"
    image     = var.opa_image
    essential = true
  }]
}

module "projector" {
  source      = "../../modules/ecs_service"
  name        = "${var.name}-projector"
  region      = var.region
  cluster_arn = aws_ecs_cluster.this.arn
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  image       = var.api_image
  command     = ["python", "-m", "enterprise_context.projection.worker"]
  cpu         = 256
  memory      = 512
  environment = {
    ENVIRONMENT                 = var.environment
    FUSEKI_URL                  = "http://fuseki.${local.namespace}:3030/enterprise"
    FUSEKI_ADMIN_USER           = "admin"
    OTEL_EXPORTER_OTLP_ENDPOINT = var.otel_exporter_otlp_endpoint
  }
  secret_arns = {
    DATABASE_URL          = "${module.postgres.database_url_secret_arn}:url::"
    FUSEKI_ADMIN_PASSWORD = aws_secretsmanager_secret.fuseki_admin.arn
  }
}

module "web" {
  source                     = "../../modules/ecs_service"
  name                       = "${var.name}-web"
  region                     = var.region
  cluster_arn                = aws_ecs_cluster.this.arn
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  image                      = var.web_image
  container_port             = 80
  cpu                        = 256
  memory                     = 512
  environment                = { API_UPSTREAM = "http://api.${local.namespace}:8000/" }
  ingress_security_group_ids = [module.alb.security_group_id]
  target_group_arn           = module.alb.web_target_group_arn
}

module "bedrock" {
  count     = length(var.bedrock_model_ids) > 0 ? 1 : 0
  source    = "../../modules/bedrock_access"
  role_name = module.api.task_role_name
  region    = var.region
  model_ids = var.bedrock_model_ids
}

module "neptune" {
  count                      = var.enable_neptune ? 1 : 0
  source                     = "../../modules/neptune"
  name                       = var.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.api.security_group_id]
  deletion_protection        = var.deletion_protection
}
