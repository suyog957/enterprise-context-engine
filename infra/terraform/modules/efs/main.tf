terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# Encrypted EFS file system for Fuseki's TDB2 database. The RDF projection is derived
# data (rebuildable from PostgreSQL, source files and the outbox), so this is a cache
# of a projection, not a system of record.

resource "aws_efs_file_system" "this" {
  creation_token   = "${var.name}-efs"
  encrypted        = true
  kms_key_id       = var.kms_key_arn
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
  tags = merge(var.tags, { Name = "${var.name}-efs" })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-efs"
  description = "NFS from the Fuseki task only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "nfs" {
  for_each                     = toset(var.allowed_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  description                  = "NFS from an allowed task"
}

resource "aws_efs_mount_target" "this" {
  count           = length(var.subnet_ids)
  file_system_id  = aws_efs_file_system.this.id
  subnet_id       = var.subnet_ids[count.index]
  security_groups = [aws_security_group.this.id]
}

resource "aws_efs_access_point" "this" {
  file_system_id = aws_efs_file_system.this.id
  posix_user {
    uid = var.posix_uid
    gid = var.posix_gid
  }
  root_directory {
    path = "/fuseki"
    creation_info {
      owner_uid   = var.posix_uid
      owner_gid   = var.posix_gid
      permissions = "750"
    }
  }
  tags = var.tags
}

resource "aws_efs_file_system_policy" "this" {
  file_system_id = aws_efs_file_system.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "*"
        Resource  = aws_efs_file_system.this.arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AllowAccessPointClients"
        Effect    = "Allow"
        Principal = { AWS = var.client_role_arns }
        Action    = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource  = aws_efs_file_system.this.arn
        Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.this.arn } }
      },
    ]
  })
}
