# ==============================================================
# EKS Cluster - Auto Mode
# ==============================================================

data "aws_availability_zones" "available" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

# ---- VPC ----
module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
  #   version = "~> 6.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = local.azs
  private_subnets = [for k, v in local.azs : cidrsubnet(var.vpc_cidr, 4, k)]
  public_subnets  = [for k, v in local.azs : cidrsubnet(var.vpc_cidr, 8, k + 48)]

  enable_nat_gateway = true
  single_nat_gateway = true # Cost-saving for dev; use one per AZ for prod

  # Tags required for EKS Auto Mode to discover subnets
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }
}

# ---- EKS Cluster (Auto Mode) ----
module "eks" {
  source = "terraform-aws-modules/eks/aws"
  #   version = "~> 20.31"

  name               = var.cluster_name
  kubernetes_version = var.cluster_version

  # Auto Mode — EKS manages node groups, kube-proxy, CoreDNS, etc.
  compute_config = {
    enabled    = true
    node_pools = ["general-purpose", "system"]
  }

  # Networking
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets
  # subnet_ids = concat(
  #     module.vpc.private_subnets,
  #     module.vpc.public_subnets
  # )

  # Security: enable private endpoint, public for initial kubectl access
  endpoint_public_access  = true
  endpoint_private_access = true

  # Auth mode required for Auto Mode
  authentication_mode = "API"

  # Security: envelope encryption for secrets at rest
  encryption_config = {
    resources = ["secrets"]
  }

  # Security: enable logging
  enabled_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler"
  ]

  # Allow current caller (your IAM user/role) to manage the cluster
  enable_cluster_creator_admin_permissions = true
}
