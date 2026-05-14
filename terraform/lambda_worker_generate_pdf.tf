#
# FILE: terraform/lambda_worker_generate_pdf.tf

resource "aws_iam_role" "generate_pdf_role" {
  name = "${var.project_name}-GeneratePDFRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "generate_pdf_policy" {
  role       = aws_iam_role.generate_pdf_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

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

resource "aws_lambda_function" "generate_pdf" {
  function_name = "${var.project_name}-GeneratePDF"
  role          = aws_iam_role.generate_pdf_role.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.pdf_generator_repo.repository_url}@sha256:8958ce9b1c3ebde72a20d2ef00994b7b2017b78b3eb9073037f96178442315b7"
  architectures = ["x86_64"]
  timeout       = 900
  memory_size   = 3008

  environment {
    variables = {
      ARTIFACTS_BUCKET = aws_s3_bucket.artifacts_bucket.id
    }
  }
}