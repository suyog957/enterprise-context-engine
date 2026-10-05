terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# OPTIONAL and NOT VALIDATED with this application. The graph layer is tested against
# Fuseki only. Neptune speaks SPARQL 1.1, but it requires SigV4-signed requests when IAM
# auth is on and lacks the Fuseki Graph Store Protocol behaviours used for versioned
# named-graph publication, so a dedicated adapter and conformance tests are needed first.

resource "aws_neptune_subnet_group" "this" {
  name       = "${var.name}-neptune"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "this" {
  name        = "${var.name}-neptune"
  description = "Neptune from application tasks only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each                     = toset(var.allowed_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  description                  = "Neptune from application security group"
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                  = "${var.name}-graph"
  engine                              = "neptune"
  engine_version                      = var.engine_version
  neptune_subnet_group_name           = aws_neptune_subnet_group.this.name
  vpc_security_group_ids              = [aws_security_group.this.id]
  storage_encrypted                   = true
  kms_key_arn                         = var.kms_key_arn
  iam_database_authentication_enabled = true
  backup_retention_period             = 7
  deletion_protection                 = var.deletion_protection
  skip_final_snapshot                 = !var.deletion_protection
  final_snapshot_identifier           = var.deletion_protection ? "${var.name}-graph-final" : null
  enable_cloudwatch_logs_exports      = ["audit"]
  tags                                = var.tags
}

resource "aws_neptune_cluster_instance" "this" {
  count              = var.instance_count
  identifier         = "${var.name}-graph-${count.index}"
  cluster_identifier = aws_neptune_cluster.this.id
  instance_class     = var.instance_class
  engine             = "neptune"
  tags               = var.tags
}
