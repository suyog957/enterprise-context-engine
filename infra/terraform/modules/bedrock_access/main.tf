terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# Least-privilege Bedrock access for the API task role: invoke only the listed models.
# The application uses the deterministic mock model unless LLM_PROVIDER=bedrock.

resource "aws_iam_role_policy" "this" {
  name = "bedrock-invoke"
  role = var.role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeApprovedModels"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
      Resource = [for model in var.model_ids : "arn:aws:bedrock:${var.region}::foundation-model/${model}"]
    }]
  })
}
