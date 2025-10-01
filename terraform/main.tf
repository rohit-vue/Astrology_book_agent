#main.tf

provider "aws" {
  region = "us-east-1"
}

variable "aws_region" {
  description = "The AWS region to deploy resources in."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Astrology Book Factory"
  type        = string
  default     = "AstrologyBookFactory"
}

variable "unique_suffix" {
  description = "A unique suffix for resource names."
  type        = string
  default     = "astrology-initials-123"
}

resource "aws_s3_bucket" "artifacts_bucket" {
  # Renamed for clarity, as it holds more than just books
  bucket = "astrology-artifacts-${var.unique_suffix}"
}

resource "aws_dynamodb_table" "orders_table" {
  name         = "${var.project_name}-Orders"
  billing_mode = "PAY_PER_REQUEST"

  # IMPORTANT: Changed Hash Key to 'order_id' to match the playbook and code.
  # Consistency between Infrastructure, Data Model, and Code is critical.
  hash_key = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
}

resource "aws_secretsmanager_secret" "api_keys_v2" {
  name        = "${var.project_name}-ApiKeys-V2"
  description = "API Keys for Astrology, OpenAI, Lulu, Shopify"
}

data "aws_caller_identity" "current" {}

# 2. ADD THIS VARIABLE
# This defines a variable so we can pass your AWS profile name
# into the local-exec command.
variable "aws_profile" {
  description = "The AWS CLI profile to use for local-exec commands."
  type        = string
  default     = "DEV3-458409718193" # I've pre-filled this with your profile name
}