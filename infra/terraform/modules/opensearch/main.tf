terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

# Fine-grained access control with an internal master user: the application's
# OpenSearch client sends basic-auth credentials (no SigV4 signing required).
resource "random_password" "master" {
  length           = 32
  special          = true
  override_special = "-_" # URL-safe: the password is embedded in OPENSEARCH_URL
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
}

resource "aws_secretsmanager_secret" "master" {
  name                    = "${var.name}/opensearch/master"
  recovery_window_in_days = 7
  kms_key_id              = var.kms_key_arn
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "master" {
  secret_id = aws_secretsmanager_secret.master.id
  secret_string = jsonencode({
    username = var.master_username
    password = random_password.master.result
    url      = "https://${var.master_username}:${random_password.master.result}@${aws_opensearch_domain.this.endpoint}"
  })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-opensearch"
  description = "OpenSearch HTTPS from application tasks only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each                     = toset(var.allowed_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "HTTPS from application security group"
}

resource "aws_cloudwatch_log_group" "slow" {
  name              = "/aws/opensearch/${var.name}/search-slow"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "log_publishing" {
  statement {
    actions   = ["logs:PutLogEvents", "logs:CreateLogStream"]
    resources = ["${aws_cloudwatch_log_group.slow.arn}:*"]
    principals {
      type        = "Service"
      identifiers = ["es.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "this" {
  policy_name     = "${var.name}-opensearch-logs"
  policy_document = data.aws_iam_policy_document.log_publishing.json
}

resource "aws_opensearch_domain" "this" {
  domain_name    = "${var.name}-search"
  engine_version = var.engine_version

  cluster_config {
    instance_type          = var.instance_type
    instance_count         = var.instance_count
    zone_awareness_enabled = var.instance_count > 1
    dynamic "zone_awareness_config" {
      for_each = var.instance_count > 1 ? [1] : []
      content {
        availability_zone_count = 2
      }
    }
  }

  vpc_options {
    subnet_ids         = slice(var.subnet_ids, 0, var.instance_count > 1 ? 2 : 1)
    security_group_ids = [aws_security_group.this.id]
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.volume_size_gb
  }

  encrypt_at_rest {
    enabled    = true
    kms_key_id = var.kms_key_arn
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true
    master_user_options {
      master_user_name     = var.master_username
      master_user_password = random_password.master.result
    }
  }

  # Network reachability is limited to the VPC and security group; fine-grained
  # access control authenticates every request.
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "*" }
      Action    = "es:ESHttp*"
      Resource  = "arn:aws:es:${var.region}:${var.account_id}:domain/${var.name}-search/*"
    }]
  })

  log_publishing_options {
    log_type                 = "SEARCH_SLOW_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.slow.arn
  }

  depends_on = [aws_cloudwatch_log_resource_policy.this]
  tags       = var.tags
}
