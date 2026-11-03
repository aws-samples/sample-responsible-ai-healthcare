# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Uncomment and configure for remote state management.
# See: https://developer.hashicorp.com/terraform/language/backend/s3
#
# terraform {
#   backend "s3" {
#     bucket       = "YOUR-TERRAFORM-STATE-BUCKET"
#     key          = "rai-healthcare-demo/terraform.tfstate"
#     region       = "us-east-1"
#     encrypt      = true
#     use_lockfile = true
#   }
# }
