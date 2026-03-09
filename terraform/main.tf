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
  default     = "luminary-prod-v1"
}

variable "factory_start_delay_hms" {
  description = "Delay between webhook receipt and factory processing start in HH:MM:SS."
  type        = string
  default     = "00:00:00"
}

resource "aws_s3_bucket" "artifacts_bucket" {
  bucket = "astrology-artifacts-${var.unique_suffix}"
}

resource "aws_dynamodb_table" "orders_table" {
  name         = "${var.project_name}-Orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
}

resource "aws_s3_bucket_public_access_block" "allow_public" {
  bucket = aws_s3_bucket.artifacts_bucket.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_secretsmanager_secret" "api_keys_v2" {
  name        = "${var.project_name}-ApiKeys-V2"
  description = "API Keys for Astrology, OpenAI, Lulu, Shopify"
}

data "aws_caller_identity" "current" {}

variable "aws_profile" {
  description = "The AWS CLI profile to use for local-exec commands."
  type        = string
  default     = "DEV3-926890291123" # I've pre-filled this with your profile name
}

resource "aws_s3_bucket_policy" "artifacts_bucket_policy" {
  bucket = aws_s3_bucket.artifacts_bucket.id
  depends_on = [aws_s3_bucket_public_access_block.allow_public]

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect    = "Allow",
        Principal = "*",
        Action    = "s3:GetObject",
        Resource  = "${aws_s3_bucket.artifacts_bucket.arn}/chapters-images/*" # Be specific to the image folder
      }
    ]
  })
}
