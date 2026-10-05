terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

# The application consumes a complete DATABASE_URL, so the password is generated here
# and the URL is stored in Secrets Manager for injection into tasks. The password is
# therefore also in Terraform state: use an encrypted, access-controlled remote backend.
resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.name}/postgres/database-url"
  recovery_window_in_days = 7
  kms_key_id              = var.kms_key_arn
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = jsonencode({
    username = var.master_username
    password = random_password.master.result
    url      = "postgresql://${var.master_username}:${random_password.master.result}@${aws_db_instance.this.endpoint}/${var.database_name}?sslmode=require"
  })
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-postgres"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "this" {
  name        = "${var.name}-postgres"
  description = "PostgreSQL access from application tasks only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each                     = toset(var.allowed_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from application security group"
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-postgres17"
  family = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier                      = "${var.name}-postgres"
  engine                          = "postgres"
  engine_version                  = var.engine_version
  instance_class                  = var.instance_class
  allocated_storage               = var.allocated_storage_gb
  max_allocated_storage           = var.max_allocated_storage_gb
  storage_type                    = "gp3"
  storage_encrypted               = true
  kms_key_id                      = var.kms_key_arn
  db_name                         = var.database_name
  username                        = var.master_username
  password                        = random_password.master.result
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.this.id]
  parameter_group_name            = aws_db_parameter_group.this.name
  publicly_accessible             = false
  multi_az                        = var.multi_az
  backup_retention_period         = var.backup_retention_days
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = !var.deletion_protection
  final_snapshot_identifier       = var.deletion_protection ? "${var.name}-postgres-final" : null
  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = true
  copy_tags_to_snapshot           = true
  tags                            = var.tags
}
