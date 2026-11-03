# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Replica Region for S3 cross-Region replication (see replication.tf)
provider "aws" {
  alias  = "replica"
  region = var.replica_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
