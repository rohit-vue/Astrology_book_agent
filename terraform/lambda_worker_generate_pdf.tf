#
# FILE: terraform/lambda_worker_generate_pdf.tf
#
# IAM Role for the GeneratePDF Lambda
resource "aws_iam_role" "generate_pdf_role" {
  name = "${var.project_name}-GeneratePDFRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# Attach a managed policy that includes permissions for ECR, VPC, and CloudWatch Logs
resource "aws_iam_role_policy_attachment" "generate_pdf_policy" {
  role       = aws_iam_role.generate_pdf_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for specific S3 access
resource "aws_iam_role_policy" "generate_pdf_permissions" {
  name = "GeneratePDFPermissions"
  role = aws_iam_role.generate_pdf_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      Effect   = "Allow",
      Resource = ["${aws_s3_bucket.artifacts_bucket.arn}", "${aws_s3_bucket.artifacts_bucket.arn}/*"]
    }]
  })
}

# The Lambda function resource, configured to use a container image.
# This block is now active and ready to be deployed.
resource "aws_lambda_function" "generate_pdf" {
  function_name = "${var.project_name}-GeneratePDF"
  role          = aws_iam_role.generate_pdf_role.arn
  package_type  = "Image"
  # This points to the image you will build and push to ECR
  image_uri = "${aws_ecr_repository.pdf_generator_repo.repository_url}:v31"

  # PDF generation is very intensive. Give it maximum resources.
  timeout     = 900 # 15 minutes
  memory_size = 3008

  environment {
    variables = {
      ARTIFACTS_BUCKET = aws_s3_bucket.artifacts_bucket.id
    }
  }
}