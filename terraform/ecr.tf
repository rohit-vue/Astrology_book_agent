#
# FILE: terraform/ecr.tf (Updated Version)
#

# This is the original repository for the PDF generator. It stays the same.
resource "aws_ecr_repository" "pdf_generator_repo" {
  name = "${lower(var.project_name)}/pdf-generator"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}

# --- ADD THIS NEW BLOCK ---
# This is the new repository specifically for our Order Ingestion Lambda container.
resource "aws_ecr_repository" "order_ingestion_repo" {
  name = "${lower(var.project_name)}/order-ingestion"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}