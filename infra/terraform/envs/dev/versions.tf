terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }

  # State contains generated secrets: use an encrypted, versioned, access-controlled
  # backend. Configure at init time, for example:
  #   tofu init -backend-config="bucket=<state-bucket>" -backend-config="key=ecg/dev.tfstate" \
  #     -backend-config="region=<region>" -backend-config="encrypt=true" \
  #     -backend-config="use_lockfile=true"
  # backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "enterprise-context-graph-agent"
      Environment = var.environment
      ManagedBy   = "terraform"
      DataClass   = "synthetic"
    }
  }
}
