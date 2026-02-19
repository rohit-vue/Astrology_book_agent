#
# FILE: terraform/ecr.tf (Updated Version)
#

resource "aws_ecr_repository" "pdf_generator_repo" {
  name = "${lower(var.project_name)}/pdf-generator"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}

resource "aws_ecr_repository" "order_ingestion_repo" {
  name = "${lower(var.project_name)}/order-ingestion"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}

resource "aws_ecr_repository" "fetch_qanda_repo" {
  name = "${lower(var.project_name)}/fetch-qanda-data"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}