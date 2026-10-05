terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# One Fargate service: task definition (main container plus optional sidecars),
# security group, log group, execution role and a least-privilege task role.

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

locals {
  # valueFrom may select a JSON key (arn:...:secret:name-abc123:url::); IAM needs the
  # base secret ARN, i.e. the first seven colon-separated segments.
  secret_resource_arns = distinct([
    for arn in values(var.secret_arns) : join(":", slice(split(":", arn), 0, 7))
  ])
}

# The execution role may read only the secrets this service injects.
resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  role  = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.secret_resource_arns
    }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "task" {
  count  = var.task_policy_json == null ? 0 : 1
  role   = aws_iam_role.task.id
  policy = var.task_policy_json
}

resource "aws_security_group" "this" {
  name        = "${var.name}-service"
  description = "Tasks for ${var.name}"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Outbound to AWS APIs and data stores"
}

resource "aws_vpc_security_group_ingress_rule" "from_sources" {
  for_each                     = var.container_port == null ? toset([]) : toset(var.ingress_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
  description                  = "Service port from an allowed security group"
}

locals {
  log_configuration = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.this.name
      awslogs-region        = var.region
      awslogs-stream-prefix = var.name
    }
  }
  main_container = {
    name                   = var.name
    image                  = var.image
    essential              = true
    command                = var.command
    portMappings           = var.container_port == null ? [] : [{ containerPort = var.container_port, protocol = "tcp" }]
    environment            = [for key, value in var.environment : { name = key, value = value }]
    secrets                = [for key, arn in var.secret_arns : { name = key, valueFrom = arn }]
    readonlyRootFilesystem = var.readonly_root_filesystem
    mountPoints            = var.efs_volume == null ? [] : [{ sourceVolume = "data", containerPath = var.efs_volume.container_path, readOnly = false }]
    logConfiguration       = local.log_configuration
    healthCheck = var.health_check_command == null ? null : {
      command     = var.health_check_command
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }
  sidecars = [for sidecar in var.sidecars : merge(sidecar, { logConfiguration = local.log_configuration })]
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode(concat([local.main_container], local.sidecars))

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  dynamic "volume" {
    for_each = var.efs_volume == null ? [] : [var.efs_volume]
    content {
      name = "data"
      efs_volume_configuration {
        file_system_id     = volume.value.file_system_id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = volume.value.access_point_id
          iam             = "ENABLED"
        }
      }
    }
  }

  tags = var.tags
}

resource "aws_ecs_service" "this" {
  name                               = var.name
  cluster                            = var.cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  enable_execute_command             = false
  propagate_tags                     = "SERVICE"
  deployment_minimum_healthy_percent = var.desired_count > 1 ? 50 : 0
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = concat([aws_security_group.this.id], var.additional_security_group_ids)
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [var.target_group_arn]
    content {
      target_group_arn = load_balancer.value
      container_name   = var.name
      container_port   = var.container_port
    }
  }

  dynamic "service_registries" {
    for_each = var.service_discovery_namespace_id == null ? [] : [1]
    content {
      registry_arn = aws_service_discovery_service.this[0].arn
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = var.tags
}

# Private DNS name (<discovery_name>.<namespace>) for service-to-service calls.
resource "aws_service_discovery_service" "this" {
  count = var.service_discovery_namespace_id == null ? 0 : 1
  name  = coalesce(var.discovery_name, var.name)
  dns_config {
    namespace_id   = var.service_discovery_namespace_id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
  health_check_custom_config {
    failure_threshold = 1
  }
  tags = var.tags
}
