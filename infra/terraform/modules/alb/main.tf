terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# Public entry point. Only the web service is exposed; its nginx proxies /api/ to the
# API over private service discovery, exactly as in the local Compose setup.

resource "aws_security_group" "this" {
  name        = "${var.name}-alb"
  description = "Public HTTP(S) to the load balancer"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = each.value
  description       = "HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value
  description       = "HTTP (redirected to HTTPS when a certificate is configured)"
}

resource "aws_vpc_security_group_egress_rule" "to_vpc" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 0
  to_port           = 65535
  cidr_ipv4         = var.vpc_cidr_block
  description       = "To targets inside the VPC"
}

resource "aws_lb" "this" {
  name                       = "${var.name}-alb"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.this.id]
  subnets                    = var.public_subnet_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  tags                       = var.tags
}

resource "aws_lb_target_group" "web" {
  name        = "${var.name}-web"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path    = "/"
    matcher = "200"
  }
  tags = var.tags
}

locals {
  https = var.certificate_arn != null
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = local.https ? "redirect" : "forward"
    target_group_arn = local.https ? null : aws_lb_target_group.web.arn
    dynamic "redirect" {
      for_each = local.https ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}
